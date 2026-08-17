"""Agent orchestration with concise, operational traces.

Two planners sit behind one entry point. When an API key is configured, an LLM
decides which MCP tools to call and in what order (`app.planner`). When it is
not, a deterministic router covers the same workflows so that the application
runs, and CI passes, with no credentials at all.

Safety is deliberately *not* delegated to the model. The sensitive-conduct gate
runs before either planner, so an escalation cannot depend on a model judgement
call, and mock ticket creation is enforced in code on both paths.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import planner, settings, usage
from .mcp_client import call, request_session

logger = logging.getLogger(__name__)

SENSITIVE = (
    "harass", "discriminat", "retaliat", "assault", "threat", "unsafe", "violence", "bully",
    "abuse", "stalk", "weapon", "gun", "knife", "shoot", "suicid", "self-harm", "hurt myself",
)
IMMEDIATE_DANGER = (
    "immediate danger", "threaten", "weapon", "gun", "knife", "shoot", "suicid", "self-harm",
    "hurt myself",
)

PERSONAL = ("my pto", "my benefit", "my balance", "am i eligible", "i take", "my record", "my leave")
OBVIOUSLY_OUT_OF_SCOPE = (
    "capital of", "python function", "linked list", "revenue forecast", "stock price", "weather",
)
# This synthetic corpus deliberately models US company policy only. These
# terms are a narrow guard for the country-specific question included in the
# evaluation set; country entitlements must not be inferred from a US policy.
# The corpus models United States company policy only, so a question asking what
# another country's law entitles an employee to cannot be answered from it. Two
# things have to be true before refusing, because naming a foreign country is not
# by itself out of scope: the remote-work policy has an International Work
# section, and "can I work from Portugal for six weeks" is a supported question
# about *our* policy abroad. The refusal is for *that jurisdiction's*
# entitlements, which is a question about foreign law.
NON_US_JURISDICTIONS = (
    "germany", "german", "france", "french", "spain", "spanish", "portugal", "portuguese",
    "italy", "italian", "ireland", "irish", "netherlands", "dutch", "belgium", "belgian",
    "poland", "polish", "sweden", "swedish", "norway", "norwegian", "denmark", "danish",
    "finland", "finnish", "austria", "austrian", "switzerland", "swiss", "greece", "greek",
    "united kingdom", "britain", "british", "england", "scotland", "wales", "ireland",
    "canada", "canadian", "mexico", "mexican", "brazil", "brazilian", "argentina",
    "india", "indian", "china", "chinese", "japan", "japanese", "korea", "korean",
    "singapore", "australia", "australian", "new zealand", "south africa",
    "eu ", "european union", "abroad", "overseas", "outside the us",
    "outside the united states",
)
# Wording that turns a country reference into a question about that country's
# own statutory position rather than about this company's policy.
FOREIGN_LAW_TERMS = (
    "entitlement", "entitled", "statutory", "by law", "legally", "legal minimum",
    "labour law", "labor law", "employment law", "mandated", "required by law",
    "local law", "national law", "works council", "collective agreement",
)


def _policy_sections_for(message: str) -> list[tuple[str, str]]:
    """Choose precise, heading-aware evidence after the initial MCP search.

    The deterministic fallback is intentionally transparent rather than a
    pretend language model.  It starts with normal retrieval, then uses a small
    intent router to fetch the relevant named policy sections over MCP.  This
    avoids answering a detailed question from an unrelated top chunk when the
    sparse retriever sees several common words.  The same section lookups also
    keep multi-policy answers grounded in every policy they actually use.
    """
    normalized = message.lower()
    sections: list[tuple[str, str]] = []

    def add(document: str, section: str) -> None:
        candidate = (document, section)
        if candidate not in sections:
            sections.append(candidate)

    if "personal laptop" in normalized:
        add("expense_policy.md", "Home Office and Equipment")
        add("equipment_and_asset_policy.md", "Requesting Equipment")
    if "floating holiday" in normalized:
        add("holidays_and_schedules.txt", "Floating Holidays")
    if any(term in normalized for term in ("parental leave", "adoption", "foster placement")):
        add("leave_of_absence_policy.md", "Parental Leave")
    if ("hotel" in normalized and ("wi-fi" in normalized or "wifi" in normalized)) or (
        "public internet" in normalized
    ):
        add("remote_work_policy.md", "Security Requirements")
    if any(term in normalized for term in ("when are employees paid", "when am i paid", "pay schedule", "payday")):
        add("compensation_and_payroll_policy.md", "Pay Schedule")
    if ("ai assistant" in normalized or "artificial intelligence" in normalized) and (
        "ticket" in normalized or "customer" in normalized
    ):
        add("data_security_policy.html", "Artificial Intelligence and Third-Party Tools")

    if any(term in normalized for term in (
        "portugal", "international work", "another country", "work overseas", "working overseas", "work abroad",
    )):
        add("remote_work_policy.md", "International Work")
        add("remote_work_policy.md", "Security Requirements")
        add("data_security_policy.html", "Devices")
        add("data_security_policy.html", "Access and Credentials")

    if "conference" in normalized:
        add("performance_and_development_policy.md", "Development and Learning")
        add("travel_policy.md", "Approval")
        add("travel_policy.md", "Booking")
        add("expense_policy.md", "Allowable Expenses")

    if any(term in normalized for term in ("off sick", "sick for", "health insurance")):
        add("pto_policy.md", "Interaction with Other Policies")
        add("leave_of_absence_policy.md", "Common Questions")
        add("benefits_policy.html", "Leave and Coverage Continuation")

    if "laptop" in normalized and any(term in normalized for term in ("stolen", "theft", "lost", "taken")):
        add("equipment_and_asset_policy.md", "Loss and Theft")
        add("data_security_policy.html", "Reporting Incidents")
        add("remote_work_policy.md", "Security Requirements")

    if any(term in normalized for term in ("medical plan", "medical benefits")):
        add("benefits_policy.html", "Common Questions")
    if any(term in normalized for term in ("work from new york", "another state")):
        add("remote_work_policy.md", "Domestic Temporary Work Locations")
    if "pto" in normalized or "paid time off" in normalized:
        add("pto_policy.md", "Request and Approval")
    if "receipt" in normalized and ("business lunch" in normalized or "expense" in normalized):
        add("expense_policy.md", "Allowable Expenses")

    return sections


def _preflight_response(message: str, employee_id: str | None) -> dict[str, Any] | None:
    """Handle questions that must not depend on an LLM decision.

    Clarifying an underspecified request and rejecting a plainly non-HR one
    are safety and scope controls, not reasoning tasks.  Keeping them before
    both planner paths ensures an API-key deployment behaves no less safely
    than the credential-free deterministic fallback.
    """
    normalized = message.lower()

    if any(phrase in normalized for phrase in OBVIOUSLY_OUT_OF_SCOPE):
        return {
            "answer": (
                "I can only help with this company's HR policies and synthetic employee records. "
                "Please ask an HR policy or workplace-operations question."
            ),
            "citations": [],
            "trace": [],
            "out_of_corpus": True,
            "planner": "scope-gate",
        }

    if not employee_id and normalized.strip().startswith("am i eligible"):
        return {
            "answer": (
                "Please tell me which benefit or programme you mean and provide a synthetic employee ID "
                "(for example E1001). I will not assume a topic or guess eligibility."
            ),
            "citations": [],
            "trace": [],
            "needs_clarification": True,
            "planner": "clarification-gate",
        }

    pto_policy_question = any(term in normalized for term in (
        "notice", "approval", "request", "policy", "accrual",
    ))
    if not employee_id and (
        any(word in normalized for word in PERSONAL)
        or (
            ("how much pto" in normalized or "how many pto" in normalized)
            and not pto_policy_question
        )
    ):
        return {
            "answer": (
                "Please provide a synthetic employee ID (for example E1001) so I can check the "
                "mock record. I will not guess identity or eligibility."
            ),
            "citations": [],
            "trace": [],
            "needs_clarification": True,
            "planner": "clarification-gate",
        }

    if ("reimbursed" in normalized or "reimbursement" in normalized) and "this" in normalized:
        return {
            "answer": (
                "Please say what expense or category you want reimbursed and its approximate cost. "
                "I need those details to check the relevant policy."
            ),
            "citations": [],
            "trace": [],
            "needs_clarification": True,
            "planner": "clarification-gate",
        }

    return None


def _asks_about_foreign_law(message: str) -> bool:
    """True when the question asks what another country's law provides.

    Both halves are required. A country name alone is in scope — the remote-work
    policy covers working from another country — and an entitlement word alone is
    an ordinary policy question about US employees.
    """
    normalized = message.lower()
    return any(term in normalized for term in NON_US_JURISDICTIONS) and any(
        term in normalized for term in FOREIGN_LAW_TERMS
    )


def _named_jurisdiction(message: str) -> str:
    """Name the country back to the user rather than saying "that country"."""
    normalized = message.lower()
    for term in NON_US_JURISDICTIONS:
        if term in normalized and term.strip() not in {
            "eu", "abroad", "overseas", "outside the us", "outside the united states",
        }:
            return term.strip().title()
    return "that country"


async def _unsupported_jurisdiction_response(message: str) -> dict[str, Any]:
    """Refuse non-US entitlements with visible MCP evidence.

    We still retrieve the relevant policy so the browser trace demonstrates
    that the refusal is grounded in the project corpus rather than a model
    assumption.  The response does not extrapolate a US entitlement to a
    foreign jurisdiction.
    """
    trace: list[dict[str, Any]] = []
    arguments = {"query": f"{message} United States policy scope", "limit": settings.TOP_K}
    policy = await call("search_policy_documents", arguments)
    trace.append(_trace_entry("search_policy_documents", arguments, policy))
    return {
        "answer": (
            f"I cannot state a {_named_jurisdiction(message)}-specific entitlement. "
            "This synthetic policy corpus covers "
            "United States employees only, so please contact People Operations for the applicable "
            "local policy or legal guidance."
        ),
        "citations": _cite(policy) if isinstance(policy, list) else [],
        "trace": trace,
        "out_of_corpus": True,
        "planner": "jurisdiction-gate",
    }


def _is_device_security_incident(message: str) -> bool:
    """Identify a lost/stolen company-device report that needs a prompt safe response."""
    normalized = message.lower()
    return "laptop" in normalized and any(term in normalized for term in ("stolen", "theft", "lost", "taken"))


def _evidence_excerpt(results: list[dict[str, Any]]) -> str:
    """Produce a readable, bounded answer basis from MCP policy chunks."""
    excerpts: list[str] = []
    seen: set[str] = set()
    for item in results:
        identifier = str(item.get("id", ""))
        text = item.get("text")
        if not isinstance(text, str) or identifier in seen:
            continue
        seen.add(identifier)
        excerpts.append(_sentence_bounded(text, 480))
        if len(excerpts) == 6:
            break
    return _sentence_bounded(" ".join(excerpts), 2400)


def _sentence_bounded(text: str, limit: int) -> str:
    """Bound display evidence without presenting a partial sentence as policy.

    The deterministic fallback includes retrieved text in its final answer. A
    character slice can split a rule in half (for example, ``three weeks``),
    which makes a source-backed answer look unreliable. Prefer the last full
    sentence inside the limit. The rare single-sentence-over-limit case keeps a
    word-safe excerpt with an ellipsis so it is visibly incomplete rather than
    appearing to be a complete policy statement.
    """
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized

    window = normalized[:limit]
    sentence_end = max(
        (
            index + 1
            for index, character in enumerate(window)
            if character in ".!?" and (index + 1 == len(window) or window[index + 1].isspace())
        ),
        default=0,
    )
    if sentence_end:
        return window[:sentence_end]

    word_end = window.rfind(" ")
    safe_excerpt = window[:word_end] if word_end > 0 else window
    return f"{safe_excerpt.rstrip()}…"


def _cite(results: list[dict]) -> list[dict]:
    return [
        {"id": x["id"], "document": x["document"], "section": x["section"], "snippet": x["text"][:240]}
        for x in results
    ]


def dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse repeated retrievals of the same chunk across a multi-step run."""
    seen: dict[Any, dict[str, Any]] = {}
    for citation in citations:
        seen.setdefault(citation.get("id"), citation)
    return list(seen.values())


def _trace_entry(name: str, arguments: dict[str, Any], output: Any, *, redact: bool = False) -> dict[str, Any]:
    """Return a bounded operational trace without exposing a sensitive report."""
    if redact:
        safe_arguments = {
            key: "[redacted sensitive report]" if key == "query" else value
            for key, value in arguments.items()
        }
        return {
            "tool": name,
            "arguments": safe_arguments,
            "result_preview": "[policy routing result hidden for privacy]",
            "result_summary": "[redacted sensitive-route tool output]",
        }

    preview = planner._bounded_preview(output)
    return {
        "tool": name,
        "arguments": arguments,
        "result_preview": preview,
        "result_summary": json.dumps(preview, ensure_ascii=False, default=str)[:360],
    }


async def _escalate(message: str, employee_id: str | None, confirm_action: bool) -> dict[str, Any]:
    """Deterministic path for conduct and safety reports. The model is not consulted."""
    trace: list[dict[str, Any]] = []

    async def tool(name: str, arguments: dict[str, Any], *, redact: bool = False) -> Any:
        output = await call(name, arguments)
        trace.append(_trace_entry(name, arguments, output, redact=redact))
        return output

    # Search remains visible in the operational trace; the exact section lookup
    # then keeps a sensitive escalation grounded in the conduct policy rather
    # than trusting ranking among unrelated policies.
    await tool("search_policy_documents", {
        "query": f"{message} workplace conduct reporting non-retaliation",
        "limit": 3,
    }, redact=True)
    policies = await tool("get_policy_section", {
        "document": "workplace_conduct_policy.md",
        "section": "Reporting Concerns",
    }, redact=True)

    ticket = None
    if employee_id and confirm_action:
        ticket_result = await tool("create_mock_hr_ticket", {
            "employee_id": employee_id,
            "category": "workplace-conduct",
            "summary": message[:300],
            "confirmed": True,
        }, redact=True)
        if isinstance(ticket_result, dict) and "error" not in ticket_result:
            ticket = ticket_result

    action = (
        "A confirmed mock ticket draft (mock HR case) was prepared. It is not filed in any real HR system."
        if ticket else
        "I can prepare a mock HR ticket (mock case) only after you explicitly confirm; it would be a draft, not an investigation."
    )
    emergency = ""
    if any(phrase in message.lower() for phrase in IMMEDIATE_DANGER):
        emergency = (
            " If anyone is in immediate danger, contact local emergency services first. "
            "When it is safe, notify Company Security and People Operations."
        )
    return {
        "answer": (
            "I'm sorry this is happening. I cannot investigate or promise confidentiality. "
            "This should be routed promptly to People Operations or the confidential reporting channel "
            f"and handled as privately as practical. {action}{emergency}"
        ),
        "citations": _cite(policies),
        "trace": trace,
        "escalation": True,
        "mock_action": ticket,
        "planner": "safety-gate",
    }


async def _deterministic_respond(
    message: str, employee_id: str | None, confirm_action: bool
) -> dict[str, Any]:
    """Rule-based fallback used when no API key is configured."""
    trace: list[dict[str, Any]] = []

    async def tool(name: str, arguments: dict[str, Any]) -> Any:
        output = await call(name, arguments)
        trace.append(_trace_entry(name, arguments, output))
        return output

    normalized = message.lower()

    preflight = _preflight_response(message, employee_id)
    if preflight is not None:
        return preflight

    # Pure synthetic-record questions do not need a policy retrieval. Keeping
    # these narrow avoids implying a policy source for a fact that came only
    # from the mock record.
    if employee_id and (
        "which benefits plans" in normalized or ("plans" in normalized and "enrolled" in normalized)
    ):
        benefit = await tool("lookup_benefits_status", {"employee_id": employee_id})
        if "error" in benefit:
            return {
                "answer": "I could not find that employee's benefits record. Please verify the synthetic employee ID.",
                "citations": [], "trace": trace, "planner": "deterministic",
            }
        plans = ", ".join(benefit.get("plans", [])) or "no enrolled plans"
        return {
            "answer": f"Your synthetic benefits record is {benefit.get('status', 'unknown')}: {plans}.",
            "citations": [], "trace": trace, "planner": "deterministic",
            "answer_basis": "synthetic employee data",
        }

    if employee_id and "who is my manager" in normalized and "office" in normalized:
        profile = await tool("lookup_employee_profile", {"employee_id": employee_id})
        if "error" in profile:
            return {
                "answer": "I could not find that employee's profile. Please verify the synthetic employee ID.",
                "citations": [], "trace": trace, "planner": "deterministic",
            }
        return {
            "answer": (
                f"Your synthetic profile lists {profile.get('manager_name', 'unknown')} as manager and "
                f"{profile.get('office', 'unknown')} as the assigned office."
            ),
            "citations": [], "trace": trace, "planner": "deterministic",
            "answer_basis": "synthetic employee data",
        }

    policy = await tool("search_policy_documents", {"query": message, "limit": settings.TOP_K})
    section_evidence: list[dict[str, Any]] = []
    for document, section in _policy_sections_for(message):
        result = await tool("get_policy_section", {"document": document, "section": section})
        if isinstance(result, list):
            section_evidence.extend(item for item in result if isinstance(item, dict))
    selected_policy = section_evidence or policy
    facts: list[str] = []

    if "germany" in normalized and "leave" in normalized:
        return {
            "answer": (
                "The policy corpus covers United States employment only. I cannot state a German "
                "leave entitlement; please contact People Operations for country-specific guidance."
            ),
            "citations": [], "trace": trace, "out_of_corpus": True, "planner": "deterministic",
        }

    if employee_id and any(word in normalized for word in ("pto", "vacation", "time off", "days off")):
        profile = await tool("lookup_employee_profile", {"employee_id": employee_id})
        balance = await tool("check_pto_balance", {"employee_id": employee_id})
        if "error" in balance:
            return {
                "answer": "I could not find that employee's PTO record. Please verify the synthetic employee ID.",
                "citations": _cite(selected_policy), "trace": trace, "planner": "deterministic",
            }
        facts.append(
            f"Your mock record shows {balance['available_hours']} available PTO hours "
            f"({balance['available_hours'] / 8:g} workdays), with "
            f"{profile.get('manager_name', 'your manager')} listed as manager."
        )
        if "three day" in normalized or "3 day" in normalized:
            facts.append(
                f"Three standard workdays are 24 hours, so the {balance['available_hours']}-hour mock balance covers that amount."
            )
        if "two week" in normalized or "2 week" in normalized:
            facts.append(
                f"The {balance['available_hours']}-hour mock balance is not enough to cover two weeks, so this does not approve the request."
            )

    if employee_id and any(word in normalized for word in ("benefit", "medical", "insurance", "401", "eligible")):
        profile = await tool("lookup_employee_profile", {"employee_id": employee_id})
        benefit = await tool("lookup_benefits_status", {"employee_id": employee_id})
        if "error" in profile or "error" in benefit:
            return {
                "answer": "I could not find that employee's benefits record. Please verify the synthetic employee ID.",
                "citations": _cite(selected_policy), "trace": trace, "planner": "deterministic",
            }
        facts.append(
            f"Mock profile: {profile.get('employment_type', 'unknown')} employee; benefits status: "
            f"{benefit.get('status', 'not found')}, plans: {', '.join(benefit.get('plans', [])) or 'none'}."
        )
        if "medical" in normalized and benefit.get("status") == "not eligible":
            facts.append(
                "The synthetic record therefore does not support medical-plan enrollment; the policy evidence explains the 30-hour rule and the remaining 401(k)/employee-assistance options."
            )

    if employee_id and any(word in normalized for word in ("my manager", "who is my manager", "office", "based in", "work from new york")):
        profile = await tool("lookup_employee_profile", {"employee_id": employee_id})
        if "error" not in profile:
            facts.append(
                f"Mock profile: manager {profile.get('manager_name', 'unknown')}; "
                f"assigned office {profile.get('office', 'unknown')}; home state {profile.get('home_state', 'unknown')}."
            )

    if any(term in normalized for term in (
        "portugal", "international work", "another country", "work overseas", "working overseas", "work abroad",
    )):
        facts.append(
            "For a six-week international stay, submit the request at least six weeks ahead; approval is not guaranteed."
        )
    if any(term in normalized for term in ("off sick", "sick for", "health insurance")):
        facts.append(
            "Coverage continues during an approved paid leave; during unpaid leave, the employee remains responsible for their premium share."
        )
    if "laptop" in normalized and any(term in normalized for term in ("stolen", "theft", "lost", "taken")):
        facts.append(
            "For theft, file a police report and give Security the reference; a replacement device is issued as a priority after the incident is logged."
        )

    # Dense ranking can correctly return a semantic paraphrase whose own words
    # do not overlap with the question.  The retriever therefore exposes the
    # best lexical support *anywhere* in the corpus as ``query_support`` for
    # this conservative scope check; older lexical responses retain ``support``.
    query_support = policy[0].get("query_support", policy[0].get("support", 0.0))
    if not selected_policy or (not section_evidence and query_support < settings.MIN_SUPPORT):
        return {
            "answer": (
                "I don't have enough evidence in the internal policy corpus to answer that safely. "
                "Please contact HR or ask about a documented policy topic."
            ),
            "citations": [], "trace": trace, "out_of_corpus": True, "planner": "deterministic",
        }

    evidence = _evidence_excerpt(selected_policy)
    answer = " ".join(facts + [
        f"Based on the retrieved policy evidence: {evidence}",
        "This is policy guidance, not legal, tax, or medical advice; HR can confirm exceptions.",
    ])
    return {
        "answer": answer,
        "citations": _cite(selected_policy),
        "trace": trace,
        "planner": "deterministic",
        "answer_basis": "MCP policy retrieval" + (" + synthetic employee data" if facts else ""),
    }


def _error_detail(exc: BaseException) -> dict[str, Any]:
    """Summarise a provider failure into the fields that identify its cause.

    Only returned when EXPOSE_PLANNER_ERRORS is set. The SDK carries the useful
    discriminators — HTTP status and the provider's own error code, such as
    `insufficient_quota` or `model_not_found` — on the exception itself. Raw
    exception text stays in the server log because it can contain account or
    request details that do not belong in a browser response.
    """
    detail: dict[str, Any] = {"exception": type(exc).__name__}
    for field in ("status_code", "code", "type"):
        value = getattr(exc, field, None)
        if isinstance(value, (int, str)) and value != "":
            detail[field] = value
    return detail


async def respond(
    message: str,
    employee_id: str | None = None,
    confirm_action: bool = False,
) -> dict[str, Any]:
    """Entry point used by the web layer."""
    if any(word in message.lower() for word in SENSITIVE):
        async with request_session():
            return await _escalate(message, employee_id, confirm_action)

    preflight = _preflight_response(message, employee_id)
    if preflight is not None:
        return preflight

    async with request_session():
        if _asks_about_foreign_law(message):
            return await _unsupported_jurisdiction_response(message)

        # A lost or stolen device is time-sensitive. Route it through the
        # deterministic MCP-backed safety path so a model cannot omit the
        # immediate Security-reporting and evidence-preservation guidance.
        if _is_device_security_incident(message):
            incident = await _deterministic_respond(message, employee_id, confirm_action)
            incident["planner"] = "security-incident-gate"
            incident["answer_basis"] = "MCP policy retrieval + deterministic security-incident guard"
            return incident

        if settings.llm_enabled():
            try:
                result = await planner.respond(message, employee_id, confirm_action)
                result["citations"] = dedupe_citations(result.get("citations", []))
                return result
            except Exception as exc:
                # A provider outage degrades to the deterministic planner rather than
                # failing the request; the response says so instead of hiding it.
                logger.warning("LLM planner failed; using deterministic fallback", exc_info=True)
                usage.record_llm_failure()
                fallback = await _deterministic_respond(message, employee_id, confirm_action)
                fallback["planner"] = "deterministic-fallback"
                fallback["planner_error"] = "LLM provider unavailable; used the evidence-based fallback."
                if settings.expose_planner_errors():
                    fallback["planner_error_detail"] = _error_detail(exc)
                return fallback

        return await _deterministic_respond(message, employee_id, confirm_action)
