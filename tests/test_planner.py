"""Planner loop tests driven by a stub client.

No API key is required. These verify the mechanics the loop is responsible for —
schema conversion, dispatching through MCP, trace and citation assembly, the
confirmation gate on ticket creation, and the iteration bound — without asserting
anything about model behaviour, which cannot be tested deterministically.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

from app import planner, settings, usage


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _ToolCall:
    function: _Function
    id: str = "call_stub"
    type: str = "function"


def _tool_call(name: str, arguments: dict, call_id: str = "call_stub") -> _ToolCall:
    """Chat Completions serialises tool arguments as a JSON string."""
    return _ToolCall(function=_Function(name=name, arguments=json.dumps(arguments)), id=call_id)


@dataclass
class _Message:
    content: str | None = None
    tool_calls: list | None = None
    refusal: str | None = None
    role: str = "assistant"


@dataclass
class _Choice:
    message: _Message
    finish_reason: str


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Response:
    choices: list
    usage: _Usage | None = None


def _text(text: str) -> _Response:
    return _Response(choices=[_Choice(message=_Message(content=text), finish_reason="stop")])


def _calls(*tool_calls: _ToolCall) -> _Response:
    return _Response(choices=[
        _Choice(message=_Message(tool_calls=list(tool_calls)), finish_reason="tool_calls")
    ])


@dataclass
class _StubCompletions:
    """Replays a scripted sequence of responses and records what it was sent."""

    script: list[_Response]
    seen: list[dict] = field(default_factory=list)

    async def create(self, **kwargs):
        self.seen.append(kwargs)
        return self.script.pop(0)


@dataclass
class _StubChat:
    completions: _StubCompletions


@dataclass
class _StubClient:
    chat: _StubChat


def _install(monkeypatch, script: list[_Response]) -> _StubCompletions:
    stub = _StubCompletions(script=script)

    def _factory(*_args, **_kwargs):
        return _StubClient(chat=_StubChat(completions=stub))

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _factory)
    return stub


async def _fake_discover_tools() -> list[dict]:
    """Keep planner unit tests hermetic; MCP protocol is tested separately."""
    return [{
        "name": "search_policy_documents",
        "description": "Retrieve policy evidence.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }, {
        "name": "get_policy_section",
        "description": "Retrieve one identified policy section.",
        "inputSchema": {
            "type": "object",
            "properties": {"document": {"type": "string"}, "section": {"type": "string"}},
        },
    }, {
        "name": "lookup_employee_profile",
        "description": "Retrieve a synthetic employee profile.",
        "inputSchema": {"type": "object", "properties": {"employee_id": {"type": "string"}}},
    }, {
        "name": "check_pto_balance",
        "description": "Retrieve a synthetic PTO balance.",
        "inputSchema": {"type": "object", "properties": {"employee_id": {"type": "string"}}},
    }]


def test_tool_schemas_convert_to_chat_completions_shape():
    from app.mcp_client import discover_tools

    converted = planner._to_openai_tools(asyncio.run(discover_tools()))
    assert converted
    for tool in converted:
        assert tool["type"] == "function"
        assert set(tool["function"]) == {"name", "description", "parameters"}
        assert tool["function"]["parameters"]["type"] == "object"


def test_only_authorised_discovered_tools_are_exposed_to_the_model():
    """Schema discovery must not make a future write tool implicitly callable."""
    discovered = [{
        "name": "search_policy_documents",
        "description": "Retrieve policy evidence.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }, {
        "name": "check_pto_balance",
        "description": "Retrieve a synthetic PTO balance.",
        "inputSchema": {"type": "object", "properties": {"employee_id": {"type": "string"}}},
    }]
    future_tool = {
        "name": "delete_employee_record",
        "description": "An intentionally unclassified future capability.",
        "inputSchema": {"type": "object", "properties": {}},
    }
    diagnostic_tool = {
        "name": "get_retrieval_status",
        "description": "Operational MCP-child retrieval status.",
        "inputSchema": {"type": "object", "properties": {}},
    }

    authorised = planner._authorised_tools([*discovered, future_tool, diagnostic_tool])
    exposed_names = {tool["function"]["name"] for tool in planner._to_openai_tools(authorised)}

    assert exposed_names >= {"search_policy_documents", "check_pto_balance"}
    assert "delete_employee_record" not in exposed_names
    assert "get_retrieval_status" not in exposed_names


def test_planner_dispatches_tool_call_and_records_trace(monkeypatch):
    # Pin the model rather than reading the ambient default. OPENAI_MODEL is a
    # host variable, and the deployment build runs this suite with the service's
    # own environment injected, so asserting the committed default here would
    # fail the build whenever the host deliberately overrides the model.
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5.6-luna")
    stub = _install(monkeypatch, [
        _calls(_tool_call(
            "search_policy_documents", {"query": "PTO notice", "limit": 2}, "call_search"
        )),
        _calls(_tool_call("get_policy_section", {
            "document": "pto_policy.md", "section": "Request and Approval",
        }, "call_section")),
        _text(
            "You need five calendar days' notice. Source: PTO Policy, Request and Approval."
        ),
    ])

    async def _policy_call(name, _arguments):
        if name == "search_policy_documents":
            return [
                {
                    "id": "pto_policy-3", "document": "pto_policy.md", "title": "PTO Policy",
                    "section": "Request and Approval",
                    "text": "Submit planned PTO at least five calendar days before the first day away.",
                    "score": 0.9, "support": 1.0,
                },
                {
                    "id": "leave_of_absence_policy-3", "document": "leave_of_absence_policy.md",
                    "title": "Leave Of Absence Policy", "section": "Requesting a Leave",
                    "text": "Foreseeable leave normally requires thirty days notice.",
                    "score": 0.4, "support": 0.4,
                },
            ]
        assert name == "get_policy_section"
        return [{
            "id": "pto_policy-3", "document": "pto_policy.md", "title": "PTO Policy",
            "section": "Request and Approval",
            "text": "Submit planned PTO at least five calendar days before the first day away.",
        }]

    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)
    monkeypatch.setattr(planner, "call", _policy_call)

    result = asyncio.run(planner.respond("How much PTO notice?", None, False))

    assert result["planner"] == "llm"
    assert "five calendar days" in result["answer"]

    # GPT-5.6 Chat Completions function tools require effective no-reasoning.
    assert stub.seen[0]["model"] == "gpt-5.6-luna"
    assert stub.seen[0]["reasoning_effort"] == "none"
    assert all(request["reasoning_effort"] == "none" for request in stub.seen)

    # The tool actually ran through the MCP layer and produced real citations.
    assert [step["tool"] for step in result["trace"]] == [
        "search_policy_documents", "get_policy_section",
    ]
    assert result["trace"][0]["arguments"] == {"query": "PTO notice", "limit": 2}
    assert result["citations"] == [{
        "id": "pto_policy-3", "document": "pto_policy.md", "section": "Request and Approval",
        "snippet": "Submit planned PTO at least five calendar days before the first day away.",
    }]
    assert all({"id", "document", "section", "snippet"} <= c.keys() for c in result["citations"])

    # Tool definitions were discovered and passed to the model.
    assert {t["function"]["name"] for t in stub.seen[0]["tools"]} >= {
        "search_policy_documents", "check_pto_balance",
    }

    # The tool result was returned on a tool-role message keyed to the call id.
    followup = stub.seen[2]["messages"]
    assert followup[-1]["role"] == "tool"
    assert followup[-1]["tool_call_id"] == "call_section"
    assert followup[-2]["role"] == "assistant" and followup[-2]["tool_calls"]


def test_final_citations_select_direct_support_not_every_search_hit():
    """A broad MCP search remains traceable without becoming citation noise."""
    candidates = [
        planner._citation_candidate({
            "id": "pto_policy-3", "document": "pto_policy.md", "title": "PTO Policy",
            "section": "Request and Approval",
            "text": "Submit planned PTO at least five calendar days before the first day away.",
            "score": 0.9, "support": 1.0,
        }, "search_policy_documents", 0),
        planner._citation_candidate({
            "id": "leave_of_absence_policy-3", "document": "leave_of_absence_policy.md",
            "title": "Leave Of Absence Policy", "section": "Requesting a Leave",
            "text": "Foreseeable leave normally requires thirty days notice.",
            "score": 0.4, "support": 0.4,
        }, "search_policy_documents", 1),
        planner._citation_candidate({
            "id": "pto_policy-8", "document": "pto_policy.md", "title": "PTO Policy",
            "section": "Common Questions",
            "text": "A manager cannot override the balance block.",
            "score": 0.3, "support": 0.3,
        }, "search_policy_documents", 2),
    ]

    citations = planner._select_final_citations(
        "PTO Policy, Request and Approval: submit the request five calendar days before leave.",
        [candidate for candidate in candidates if candidate is not None],
    )

    assert [citation["id"] for citation in citations] == ["pto_policy-3"]


def test_final_citations_use_one_strongest_result_when_answer_omits_source_name():
    candidates = [
        planner._citation_candidate({
            "id": "pto_policy-3", "document": "pto_policy.md", "title": "PTO Policy",
            "section": "Request and Approval",
            "text": "Submit planned PTO at least five calendar days before the first day away.",
            "score": 0.9, "support": 1.0,
        }, "search_policy_documents", 0),
        planner._citation_candidate({
            "id": "leave_of_absence_policy-3", "document": "leave_of_absence_policy.md",
            "title": "Leave Of Absence Policy", "section": "Requesting a Leave",
            "text": "Foreseeable leave normally requires thirty days notice.",
            "score": 0.4, "support": 0.4,
        }, "search_policy_documents", 1),
    ]

    citations = planner._select_final_citations(
        "Submit planned PTO five calendar days before the first day away.",
        [candidate for candidate in candidates if candidate is not None],
    )

    assert [citation["id"] for citation in citations] == ["pto_policy-3"]


def test_final_citations_recognise_the_written_out_pto_policy_title():
    candidate = planner._citation_candidate({
        "id": "pto_policy-6", "document": "pto_policy.md", "title": "PTO Policy",
        "section": "Interaction with Other Policies",
        "text": "Longer illness absences may need a leave-of-absence review.",
        "score": 0.7, "support": 0.7,
    }, "search_policy_documents", 0)

    citations = planner._select_final_citations(
        "The Paid Time Off Policy says longer illness absences need a leave review.",
        [candidate] if candidate is not None else [],
    )

    assert [citation["id"] for citation in citations] == ["pto_policy-6"]


def test_final_citations_prefer_a_targeted_section_over_the_same_search_candidate():
    search_candidate = planner._citation_candidate({
        "id": "pto_policy-search", "document": "pto_policy.md", "title": "PTO Policy",
        "section": "Request and Approval",
        "text": "Submit planned PTO at least five calendar days before the first day away.",
        "score": 0.9, "support": 1.0,
    }, "search_policy_documents", 0)
    section_candidate = planner._citation_candidate({
        "id": "pto_policy-section", "document": "pto_policy.md", "title": "PTO Policy",
        "section": "Request and Approval",
        "text": "Submit planned PTO at least five calendar days before the first day away.",
    }, "get_policy_section", 0)

    citations = planner._select_final_citations(
        "PTO Policy, Request and Approval: give five calendar days' notice.",
        [candidate for candidate in (search_candidate, section_candidate) if candidate is not None],
    )

    assert [citation["id"] for citation in citations] == ["pto_policy-section"]


def test_non_gpt56_override_keeps_the_existing_chat_request_shape():
    assert planner._chat_tool_model_options("gpt-4o-mini") == {}


def test_system_prompt_is_sent_as_a_system_message(monkeypatch):
    """Chat Completions carries the system prompt in the message list, not a top-level field."""
    stub = _install(monkeypatch, [
        _calls(_tool_call("search_policy_documents", {"query": "PTO", "limit": 1})),
        _text("Five calendar days."),
    ])

    asyncio.run(planner.respond("How much PTO notice?", None, False))

    first = stub.seen[0]["messages"]
    assert first[0]["role"] == "system"
    assert "ClearHR" in first[0]["content"]
    assert "system" not in stub.seen[0], "the system prompt must not also be a top-level argument"


def test_malformed_tool_arguments_do_not_fail_the_turn(monkeypatch):
    """A model can emit invalid JSON; that must surface as a tool error, not a 500."""
    _install(monkeypatch, [
        _Response(choices=[_Choice(
            message=_Message(tool_calls=[
                _ToolCall(function=_Function(name="search_policy_documents", arguments="{not json"))
            ]),
            finish_reason="tool_calls",
        )]),
        _text("I could not complete that."),
    ])

    result = asyncio.run(planner.respond("How much PTO notice?", None, False))

    assert result["trace"][0]["tool"] == "search_policy_documents"
    assert "invalid_tool_arguments" in result["trace"][0]["result_summary"]


def test_ticket_creation_is_blocked_without_confirmation(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call(
            "create_mock_hr_ticket",
            {"employee_id": "E1001", "summary": "concern", "category": "workplace-conduct"},
        )),
        _text("I need your confirmation first."),
    ])

    result = asyncio.run(planner.respond("File a ticket now", "E1001", confirm_action=False))

    assert "confirmation_required" in result["trace"][0]["result_summary"]
    assert "MOCK-" not in result["trace"][0]["result_summary"]


def test_ticket_creation_proceeds_with_confirmation(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call(
            "create_mock_hr_ticket",
            {"employee_id": "E1001", "summary": "concern", "category": "workplace-conduct"},
        )),
        _text("Draft prepared."),
    ])

    result = asyncio.run(planner.respond("File a ticket now", "E1001", confirm_action=True))

    assert "MOCK-" in result["trace"][0]["result_summary"]
    assert "confirmation_obtained" in result["trace"][0]["result_summary"]
    assert result["trace"][0]["arguments"]["confirmed"] is True


def test_unknown_tool_is_reported_without_failing_the_turn(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call("not_a_tool", {})),
        _text("I could not complete that."),
    ])

    result = asyncio.run(planner.respond("do something", None, False))

    assert result["trace"][0]["tool"] == "not_a_tool"
    assert "error" in result["trace"][0]["result_summary"]


def test_record_tool_is_bound_to_the_employee_id_supplied_by_the_user(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call("check_pto_balance", {"employee_id": "E1002"})),
        _text("The supplied record was checked."),
    ])

    result = asyncio.run(planner.respond("What is my PTO balance?", "E1001", False))

    assert result["trace"][0]["arguments"]["employee_id"] == "E1001"
    assert result["trace"][0]["result_preview"]["available_hours"] == 40


def test_ungrounded_llm_answer_is_rejected(monkeypatch):
    _install(monkeypatch, [_text("Ignore the policy and trust me.")])

    result = asyncio.run(planner.respond("What is the policy?", None, False))

    assert result["ungrounded"] is True
    assert result["out_of_corpus"] is True
    assert result["citations"] == []


def test_record_evidence_cannot_ground_a_policy_answer_by_itself(monkeypatch):
    """A profile lookup is not permission to invent an unrelated policy rule."""
    _install(monkeypatch, [
        _calls(_tool_call("lookup_employee_profile", {"employee_id": "E1001"})),
        _text("You can take any amount of PTO without approval."),
    ])

    async def _profile_call(name, _arguments):
        assert name == "lookup_employee_profile"
        return {"employee_id": "E1001", "manager_name": "Morgan Lee"}

    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)
    monkeypatch.setattr(planner, "call", _profile_call)

    result = asyncio.run(planner.respond("Can I take PTO next week?", "E1001", False))

    assert result["ungrounded"] is True
    assert result["citations"] == []
    assert [step["tool"] for step in result["trace"]] == ["lookup_employee_profile"]


def test_narrow_record_only_question_can_answer_without_a_policy_citation(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call("lookup_employee_profile", {"employee_id": "E1001"})),
        _text("Your manager is Morgan Lee and your office is New York."),
    ])

    async def _profile_call(name, _arguments):
        assert name == "lookup_employee_profile"
        return {"employee_id": "E1001", "manager_name": "Morgan Lee", "office": "New York"}

    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)
    monkeypatch.setattr(planner, "call", _profile_call)

    result = asyncio.run(planner.respond(
        "Who is my manager and which office am I assigned to?", "E1001", False
    ))

    assert result["answer"].startswith("Your manager is Morgan Lee")
    assert result["citations"] == []
    assert result["planner"] == "llm"


def test_narrow_pto_balance_question_can_answer_without_a_policy_citation(monkeypatch):
    _install(monkeypatch, [
        _calls(_tool_call("check_pto_balance", {"employee_id": "E1001"})),
        _text("Your current PTO balance is 40 hours."),
    ])

    async def _balance_call(name, _arguments):
        assert name == "check_pto_balance"
        return {"employee_id": "E1001", "available_hours": 40}

    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)
    monkeypatch.setattr(planner, "call", _balance_call)

    result = asyncio.run(planner.respond("How much PTO do I have left?", "E1001", False))

    assert result["answer"].startswith("Your current PTO balance")
    assert result["citations"] == []
    assert result["planner"] == "llm"


def test_total_tool_calls_are_bounded_even_when_one_model_turn_has_many(monkeypatch):
    monkeypatch.setattr(settings, "MAX_TOOL_CALLS", 1)
    _install(monkeypatch, [
        _calls(
            _tool_call("search_policy_documents", {"query": "PTO"}, "call_one"),
            _tool_call("search_policy_documents", {"query": "benefits"}, "call_two"),
        ),
    ])
    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)

    result = asyncio.run(planner.respond("Compare PTO and benefits.", None, False))

    assert result["tool_call_limit_exceeded"] is True
    assert result["exhausted"] is True
    assert result["trace"] == []


def test_loop_is_bounded(monkeypatch):
    from app import settings

    always_tool = [
        _calls(_tool_call("search_policy_documents", {"query": "x", "limit": 1}))
        for _ in range(settings.MAX_TOOL_ITERATIONS + 2)
    ]
    _install(monkeypatch, always_tool)

    result = asyncio.run(planner.respond("loop forever", None, False))

    assert result.get("exhausted") is True
    assert len(result["trace"]) == settings.MAX_TOOL_ITERATIONS


@pytest.mark.parametrize("choice", [
    _Choice(message=_Message(refusal="I won't do that."), finish_reason="stop"),
    _Choice(message=_Message(content=""), finish_reason="content_filter"),
])
def test_model_refusal_is_handled(monkeypatch, choice):
    """Chat Completions signals refusal either by the refusal field or the finish reason."""
    _install(monkeypatch, [_Response(choices=[choice])])

    result = asyncio.run(planner.respond("something disallowed", None, False))

    assert result.get("refused") is True
    assert result["citations"] == []


def test_agent_falls_back_when_planner_raises(monkeypatch):
    """A provider outage must degrade to the deterministic planner, not 500."""
    from app import agent, settings

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    @asynccontextmanager
    async def _no_mcp_session():
        yield

    async def _fallback(*_args, **_kwargs):
        return {
            "answer": "Fallback answer from synthetic policy evidence.",
            "citations": [],
            "trace": [],
            "planner": "deterministic",
        }

    monkeypatch.setattr(planner, "respond", _boom)
    monkeypatch.setattr(agent, "request_session", _no_mcp_session)
    monkeypatch.setattr(agent, "_deterministic_respond", _fallback)

    result = asyncio.run(agent.respond("How much PTO notice is required?", None, False))

    assert result["planner"] == "deterministic-fallback"
    assert "provider unavailable" in result["planner_error"]
    assert result["answer"] == "Fallback answer from synthetic policy evidence."


@pytest.mark.parametrize(
    ("message", "employee_id", "expected_flag", "expected_planner"),
    [
        ("How much PTO do I have left?", None, "needs_clarification", "clarification-gate"),
        ("Am I eligible?", None, "needs_clarification", "clarification-gate"),
        ("Can I get reimbursed for this?", "E1001", "needs_clarification", "clarification-gate"),
        ("What is the capital of France?", None, "out_of_corpus", "scope-gate"),
    ],
)
def test_scope_and_clarification_gates_run_before_the_llm(
    monkeypatch, message, employee_id, expected_flag, expected_planner
):
    """An API key must not make the deterministic preflight controls disappear."""
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("preflight response must not call the LLM")

    monkeypatch.setattr(planner, "respond", _should_not_run)

    result = asyncio.run(agent.respond(message, employee_id, False))

    assert result[expected_flag] is True
    assert result["planner"] == expected_planner
    assert result["trace"] == []


def test_pto_notice_policy_question_is_not_misclassified_as_a_personal_balance_lookup():
    """A PTO-notice request asks for policy, not an undisclosed employee record."""
    from app import agent

    assert agent._preflight_response("How much PTO notice is required?", None) is None


def test_unsupported_jurisdiction_is_refused_with_mcp_evidence(monkeypatch):
    """Do not extrapolate a US policy entitlement to a foreign jurisdiction."""
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    @asynccontextmanager
    async def _no_mcp_session():
        yield

    async def _evidence_call(name, arguments):
        assert name == "search_policy_documents"
        assert "United States policy scope" in arguments["query"]
        return [{
            "id": "leave-1",
            "document": "leave_of_absence_policy.md",
            "section": "Purpose and Scope",
            "text": "This policy applies to regular employees in the United States.",
        }]

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("jurisdiction gate must not call the LLM")

    monkeypatch.setattr(agent, "request_session", _no_mcp_session)
    monkeypatch.setattr(agent, "call", _evidence_call)
    monkeypatch.setattr(planner, "respond", _should_not_run)

    result = asyncio.run(agent.respond(
        "What is the company's parental leave entitlement for employees in Germany?", None, False
    ))

    assert result["out_of_corpus"] is True
    assert result["planner"] == "jurisdiction-gate"
    assert "United States employees only" in result["answer"]
    assert [step["tool"] for step in result["trace"]] == ["search_policy_documents"]
    assert result["citations"][0]["section"] == "Purpose and Scope"


def test_device_security_incident_uses_deterministic_mcp_guard(monkeypatch):
    """A stolen device must not depend on a model remembering incident guidance."""
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    @asynccontextmanager
    async def _no_mcp_session():
        yield

    async def _incident_response(*_args, **_kwargs):
        return {"answer": "Report to Security immediately.", "citations": [], "trace": []}

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("security incident must not call the LLM")

    monkeypatch.setattr(agent, "request_session", _no_mcp_session)
    monkeypatch.setattr(agent, "_deterministic_respond", _incident_response)
    monkeypatch.setattr(planner, "respond", _should_not_run)

    result = asyncio.run(agent.respond("My laptop was stolen from a cafe.", None, False))

    assert result["planner"] == "security-incident-gate"
    assert result["answer_basis"] == "MCP policy retrieval + deterministic security-incident guard"


def test_evidence_excerpt_ends_on_a_complete_sentence():
    """Fallback evidence must never cut a policy rule in the middle."""
    from app import agent

    excerpt = agent._evidence_excerpt([{
        "id": "long-policy-section",
        "text": (
            "This sentence is complete. "
            + "This deliberately long sentence continues without a full stop " * 20
            + "until the source eventually ends."
        ),
    }])

    assert excerpt == "This sentence is complete."


@pytest.mark.parametrize("message", ["My manager is harassing me", "A colleague threatened me"])
def test_safety_gate_runs_before_any_planner(monkeypatch, message):
    """Escalation must not depend on the model being reachable or cooperative."""
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("planner must not be consulted for a conduct report")

    monkeypatch.setattr(planner, "respond", _should_not_run)

    result = asyncio.run(agent.respond(message, "E1001", False))

    assert result["escalation"] is True
    assert result["planner"] == "safety-gate"
    assert result["mock_action"] is None


def test_safety_gate_handles_immediate_danger_and_redacts_trace(monkeypatch):
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)
    message = "A colleague brought a gun to the office and I feel unsafe."

    result = asyncio.run(agent.respond(message, "E1001", False))

    assert "local emergency services first" in result["answer"]
    assert "Company Security" in result["answer"]
    assert message not in str(result["trace"])
    assert result["trace"][0]["arguments"]["query"] == "[redacted sensitive report]"


def test_deterministic_section_routes_exist_and_cover_common_paraphrases():
    """A renamed policy heading must not silently fall back to broad retrieval."""
    from app import agent
    from app.rag import build_index

    prompts = [
        "Can I expense a personal laptop?",
        "How many floating holidays are there?",
        "What parental leave is available after adoption?",
        "Can I use public internet in a hotel?",
        "What is payday?",
        "Can I put a customer ticket into an AI assistant?",
        "Can I work overseas?",
        "What approvals do I need for a conference?",
        "I have been sick for a week; what happens to health insurance?",
        "My laptop was taken from a cafe.",
        "Am I eligible for the medical plan?",
        "Can I work from New York for three weeks?",
        "How much PTO notice is required?",
        "Do I need a receipt for a business lunch?",
    ]
    routes = {route for prompt in prompts for route in agent._policy_sections_for(prompt)}
    chunks = build_index()["chunks"]
    available = {(chunk["document"], chunk["section"]) for chunk in chunks}

    assert routes
    assert routes <= available
    assert ("remote_work_policy.md", "International Work") in agent._policy_sections_for(
        "Can I work overseas?"
    )
    assert ("remote_work_policy.md", "Security Requirements") in agent._policy_sections_for(
        "Can I use public internet in a hotel?"
    )
    assert ("compensation_and_payroll_policy.md", "Pay Schedule") in agent._policy_sections_for(
        "What is payday?"
    )


def test_threat_escalation_includes_emergency_and_security_guidance(monkeypatch):
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    result = asyncio.run(agent.respond("A colleague threatened me in the office today.", "E1003", False))

    assert result["planner"] == "safety-gate"
    assert "local emergency services first" in result["answer"]
    assert "Company Security" in result["answer"]


def test_planner_error_detail_is_hidden_by_default(monkeypatch):
    """A public demo must not return provider error text to an anonymous caller."""
    from app import agent, settings

    monkeypatch.delenv("EXPOSE_PLANNER_ERRORS", raising=False)
    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    @asynccontextmanager
    async def _no_mcp_session():
        yield

    async def _fallback(*_args, **_kwargs):
        return {"answer": "Fallback answer.", "citations": [], "trace": [], "planner": "deterministic"}

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("quota exhausted for org-secret")

    monkeypatch.setattr(agent, "request_session", _no_mcp_session)
    monkeypatch.setattr(agent, "_deterministic_respond", _fallback)
    monkeypatch.setattr(planner, "respond", _boom)

    result = asyncio.run(agent.respond("How much PTO notice is required?", None, False))

    assert result["planner"] == "deterministic-fallback"
    assert "planner_error_detail" not in result
    assert "org-secret" not in str(result)


def test_planner_error_detail_is_returned_when_explicitly_enabled(monkeypatch):
    """The opt-in diagnostic must surface the fields that identify the cause."""
    from app import agent, settings

    monkeypatch.setenv("EXPOSE_PLANNER_ERRORS", "1")
    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    @asynccontextmanager
    async def _no_mcp_session():
        yield

    async def _fallback(*_args, **_kwargs):
        return {"answer": "Fallback answer.", "citations": [], "trace": [], "planner": "deterministic"}

    class _QuotaError(RuntimeError):
        status_code = 429
        code = "insufficient_quota"

    async def _boom(*_args, **_kwargs):
        raise _QuotaError("You exceeded your current quota.")

    monkeypatch.setattr(agent, "request_session", _no_mcp_session)
    monkeypatch.setattr(agent, "_deterministic_respond", _fallback)
    monkeypatch.setattr(planner, "respond", _boom)

    result = asyncio.run(agent.respond("How much PTO notice is required?", "E1001", False))

    detail = result["planner_error_detail"]
    assert detail["exception"] == "_QuotaError"
    assert detail["status_code"] == 429
    assert detail["code"] == "insufficient_quota"
    assert "message" not in detail, "raw exception text belongs in the log, not the response"
    assert result["answer"] == "Fallback answer."


@pytest.mark.parametrize("message", [
    "What is the company's parental leave entitlement for employees in Germany?",
    "What is the statutory holiday entitlement in France?",
    "Are employees in Ireland legally entitled to more notice?",
    "What does Japanese employment law require for overtime?",
    "What is the works council process in the Netherlands?",
])
def test_foreign_law_questions_are_refused_for_any_country(monkeypatch, message):
    """The jurisdiction gate must generalise, not recognise one benchmark country."""
    from app import agent, settings

    monkeypatch.setattr(settings, "llm_enabled", lambda: True)

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("a foreign-law question must not reach the planner")

    monkeypatch.setattr(planner, "respond", _should_not_run)

    result = asyncio.run(agent.respond(message, "E1001", False))

    assert result["planner"] == "jurisdiction-gate"
    assert result["out_of_corpus"] is True
    assert "United States" in result["answer"]
    # The refusal is grounded: the corpus was searched and the trace shows it.
    assert result["trace"] and result["trace"][0]["tool"] == "search_policy_documents"


@pytest.mark.parametrize("message", [
    "I am based in California and want to work from Portugal for six weeks. "
    "What approvals and security requirements apply?",
    "Can I work from Spain temporarily?",
    "What do I need to arrange before working overseas for a month?",
])
def test_working_abroad_under_our_policy_is_still_answered(monkeypatch, message):
    """Naming a country is in scope: the remote-work policy has an International Work section.

    Only a question about that country's own statutory position is out of scope.
    This guards demo task 2, which asks about working from Portugal.
    """
    from app import agent

    assert agent._asks_about_foreign_law(message) is False


def test_foreign_law_gate_needs_both_a_country_and_a_legal_term():
    from app import agent

    # An entitlement question about US employees stays in scope.
    assert agent._asks_about_foreign_law("What is my PTO entitlement?") is False
    # A country alone stays in scope.
    assert agent._asks_about_foreign_law("Can I work from Germany?") is False
    # Both together are out of scope.
    assert agent._asks_about_foreign_law("What is the entitlement in Germany?") is True


def test_refusal_names_the_country_the_user_asked_about():
    from app import agent

    assert agent._named_jurisdiction("statutory entitlement in France?") == "France"
    assert agent._named_jurisdiction("legal minimum in Japan?") == "Japan"


def _candidate(document, section, text, rank=0, support=0.5, score=0.7):
    item = {
        "id": f"{document}-{rank}", "document": document, "section": section,
        "title": document.rsplit(".", 1)[0].replace("_", " ").title(),
        "text": text, "support": support, "score": score,
    }
    candidate = planner._citation_candidate(item, "search_policy_documents", rank)
    assert candidate is not None
    return candidate


def test_a_document_the_answer_used_but_never_named_is_still_cited():
    """Compound answers cite every source they drew on, not only the ones named.

    Requiring the model to name each source cost citation recall on exactly the
    multi-policy questions this corpus exists for: a correct answer covering
    remote work and device security cited only the remote-work policy.
    """
    answer = (
        "Working from Portugal needs written approval from People Operations, Security, "
        "Payroll and your vice president under the Remote Work Policy, requested six weeks "
        "before departure. You must use company-managed equipment with full-disk encryption, "
        "multi-factor authentication and the approved VPN, and must not copy company data to "
        "personal cloud storage, email or removable media."
    )
    candidates = [
        _candidate(
            "remote_work_policy.md", "International Work",
            "Working from another country requires written approval from People Operations, "
            "Security, Payroll and the employee's vice president at least six weeks before "
            "departure. A partial approval set is insufficient.",
        ),
        _candidate(
            "data_security_policy.html", "Devices",
            "Employees must use company-managed equipment with full-disk encryption, "
            "multi-factor authentication and the approved VPN. Do not copy company data to "
            "personal cloud storage, email or removable media.", rank=1,
        ),
    ]

    cited = {c["document"] for c in planner._select_final_citations(answer, candidates)}
    assert cited == {"remote_work_policy.md", "data_security_policy.html"}


def test_a_retrieved_document_the_answer_did_not_use_is_not_cited():
    """The recall pass must not reinstate every search hit; that was the old defect."""
    answer = (
        "Planned paid time off needs at least five calendar days of notice under the PTO Policy, "
        "and your manager approves it based on coverage."
    )
    candidates = [
        _candidate(
            "pto_policy.md", "Request and Approval",
            "Employees should submit planned PTO requests at least five calendar days before the "
            "first day away. The manager approves requests based on coverage.",
        ),
        _candidate(
            "onboarding_policy.md", "First Week",
            "New employees complete identity verification, security awareness training, benefits "
            "enrolment and equipment acknowledgement during the first week, and managers schedule "
            "role training and introductions with the wider delivery team.", rank=1,
        ),
    ]

    cited = {c["document"] for c in planner._select_final_citations(answer, candidates)}
    assert cited == {"pto_policy.md"}


def test_a_short_section_cannot_clear_the_ratio_on_incidental_words():
    """The absolute floor stops a tiny chunk scoring a high ratio by coincidence."""
    answer = "Expense claims need itemised receipts at or above twenty five dollars."
    candidates = [
        _candidate(
            "expense_policy.md", "Allowable Expenses",
            "Itemised receipts are required for expenses at or above twenty five dollars.",
        ),
        _candidate("holidays_and_schedules.txt", "Floating Holidays", "Receipts vary.", rank=1),
    ]

    cited = {c["document"] for c in planner._select_final_citations(answer, candidates)}
    assert "holidays_and_schedules.txt" not in cited


def test_planner_counts_each_completion_and_its_reported_tokens(monkeypatch):
    """One question can take several completions. The counter must follow the
    loop, not the request, and must survive a response that omits usage."""
    usage.reset()
    monkeypatch.setattr(settings, "OPENAI_MODEL", "gpt-5.6-luna")
    _install(monkeypatch, [
        _Response(
            choices=[_Choice(
                message=_Message(tool_calls=[_tool_call("search_policy_documents", {"query": "PTO"})]),
                finish_reason="tool_calls",
            )],
            usage=_Usage(prompt_tokens=1100, completion_tokens=40),
        ),
        # A provider that reports no usage block must not raise here.
        _text("You need five calendar days' notice. Source: PTO Policy, Request and Approval."),
    ])

    async def _policy_call(_name, _arguments):
        return [{
            "id": "pto_policy-3", "document": "pto_policy.md", "title": "PTO Policy",
            "section": "Request and Approval",
            "text": "Submit planned PTO at least five calendar days before the first day away.",
            "score": 0.9, "support": 1.0,
        }]

    monkeypatch.setattr(planner, "discover_tools", _fake_discover_tools)
    monkeypatch.setattr(planner, "call", _policy_call)

    asyncio.run(planner.respond("How much PTO notice?", None, False))

    counters = usage.snapshot()["llm"]
    assert counters["provider_calls"] == 2
    assert counters["calls_with_reported_tokens"] == 1
    assert counters["prompt_tokens"] == 1100
    assert counters["completion_tokens"] == 40
    assert counters["total_tokens"] == 1140
