import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app import main, settings, usage


def _request(client: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/chat",
            "headers": [],
            "client": (client, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def clear_rate_limit() -> None:
    main._reset_chat_rate_limit()
    yield
    main._reset_chat_rate_limit()


def test_health_returns_safe_503_when_mcp_is_unavailable(monkeypatch):
    async def unavailable(**_kwargs):
        raise RuntimeError("MCP startup failed: internal-token-value")

    monkeypatch.setattr(main, "discover_tools", unavailable)

    result = asyncio.run(main.health())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    body = json.loads(result.body)
    assert body["status"] == "unavailable"
    assert body["mcp_connected"] is False
    # The degraded body may carry non-secret deployment facts such as the active
    # retriever, but never the exception text, which can name internal detail.
    assert set(body) <= {
        "status", "mcp_connected", "rag_backend", "configured_rag_backend", "commit",
    }
    assert b"internal-token-value" not in result.body


def test_chat_rate_limit_returns_429_and_retry_after(monkeypatch):
    async def successful_response(*_args):
        return {"answer": "Synthetic answer"}

    monkeypatch.setattr(main, "respond", successful_response)
    monkeypatch.setattr(main, "CHAT_RATE_LIMIT", 2)
    monkeypatch.setattr(main, "GLOBAL_CHAT_RATE_LIMIT", 20)
    payload = main.ChatRequest(message="Can I take PTO?")

    for _ in range(2):
        response = Response()
        assert asyncio.run(main.chat(payload, _request(), response))["answer"] == "Synthetic answer"
        assert response.headers["cache-control"] == "no-store"

    with pytest.raises(HTTPException) as raised:
        asyncio.run(main.chat(payload, _request(), Response()))

    assert raised.value.status_code == 429
    assert raised.value.detail == "Too many requests. Please wait and try again."
    assert int(raised.value.headers["Retry-After"]) >= 1
    assert raised.value.headers["Cache-Control"] == "no-store"


def test_chat_forwards_explicit_mock_confirmation(monkeypatch):
    received: dict[str, object] = {}

    async def capture(message: str, employee_id: str | None, confirm_action: bool):
        received.update(
            message=message,
            employee_id=employee_id,
            confirm_action=confirm_action,
        )
        return {"answer": "Mock draft prepared"}

    monkeypatch.setattr(main, "respond", capture)
    payload = main.ChatRequest(
        message="Please help with a workplace concern.",
        employee_id="E1001",
        confirm_mock_action=True,
    )

    result = asyncio.run(main.chat(payload, _request(), Response()))

    assert result["answer"] == "Mock draft prepared"
    assert received == {
        "message": "Please help with a workplace concern.",
        "employee_id": "E1001",
        "confirm_action": True,
    }


def test_chat_hides_backend_exception_details(monkeypatch):
    async def unavailable(*_args):
        raise RuntimeError("provider secret: do-not-return-this")

    monkeypatch.setattr(main, "respond", unavailable)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            main.chat(main.ChatRequest(message="Can I take PTO?"), _request(), Response())
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == "The HR tool service is temporarily unavailable. Please retry."
    assert "do-not-return-this" not in str(raised.value.detail)


def test_demo_page_marks_data_as_synthetic_and_sends_confirmation():
    page = (Path(main.__file__).parent / "static" / "index.html").read_text()

    assert "Synthetic demo only" in page
    assert "do not enter real personal information" in page
    assert 'id="confirm-mock-action"' in page
    assert "confirm_mock_action: confirmationInput.checked" in page
    assert "confirmationInput.checked = false" in page


def test_demo_page_includes_an_accurate_workflow_map():
    page = (Path(main.__file__).parent / "static" / "index.html").read_text()

    assert 'id="workflow-map"' in page
    assert "Agent orchestrator" in page
    assert "OpenAI LLM planner" in page
    assert "MCP client → FastMCP server" in page
    assert "Structured results return to the LLM" in page
    assert "final answer, citations, and exact MCP tool trace" in page


def test_health_reports_the_mcp_child_rag_backend(monkeypatch):
    """Health must query the child; a parent setting alone is not evidence."""
    from app import settings

    async def tools(**_kwargs):
        return [{"name": "get_retrieval_status"}]

    async def child_status():
        return {
            "rag_backend": "dense",
            "index_backend": "dense",
            "rag_model": "BAAI/bge-small-en-v1.5",
            "rag_index": "index.dense.json",
            "rag_index_version": 5,
            "rag_chunks": 142,
            "rag_dimensions": 384,
            "rag_provider": "fastembed",
            "rag_storage": "local-json-dense-vector-index",
            "dense_encoder_loaded": True,
        }

    monkeypatch.setattr(settings, "rag_backend", lambda: "dense")
    monkeypatch.setattr(main, "discover_tools", tools)
    monkeypatch.setattr(main.mcp_client, "retrieval_status", child_status)

    payload = asyncio.run(main.health())

    assert payload["rag_backend"] == "dense"
    assert payload["configured_rag_backend"] == "dense"
    assert payload["rag_status_source"] == "mcp_child"
    assert payload["rag_model"] == "BAAI/bge-small-en-v1.5"
    assert payload["dense_encoder_loaded"] is True
    assert payload["status"] == "ok"


def test_health_reports_the_host_injected_commit(monkeypatch):
    """The deployed build must be identifiable from the URL, not just the host UI."""
    from app import settings

    async def tools(**_kwargs):
        return [{"name": "get_retrieval_status"}]

    async def child_status():
        return {"rag_backend": "dense", "index_backend": "dense"}

    monkeypatch.setattr(settings, "rag_backend", lambda: "dense")
    monkeypatch.setattr(main, "discover_tools", tools)
    monkeypatch.setattr(main.mcp_client, "retrieval_status", child_status)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "75c0fcf1234567890abcdef")

    assert asyncio.run(main.health())["commit"] == "75c0fcf"


def test_startup_logs_the_commit_and_backend(monkeypatch, caplog):
    """The host log must identify the running build, not just the access lines."""
    import logging

    async def noop():
        return None

    monkeypatch.setattr(main.mcp_client, "startup", noop)
    monkeypatch.setattr(main.mcp_client, "shutdown", noop)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "8df81bbdeadbeef")

    async def boot():
        async with main.lifespan(main.app):
            pass

    with caplog.at_level(logging.INFO, logger="app.main"):
        asyncio.run(boot())

    assert "commit=8df81bb" in caplog.text


def test_deployed_commit_falls_back_when_no_host_variable_is_set(monkeypatch):
    """Local runs and CI have no host revision; report that instead of guessing."""
    from app import settings

    for name in ("RENDER_GIT_COMMIT", "RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)

    assert settings.deployed_commit() == "unknown"

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567")
    assert settings.deployed_commit() == "abcdef1"


def test_health_returns_safe_503_when_parent_and_child_backend_disagree(monkeypatch):
    async def tools(**_kwargs):
        return [{"name": "get_retrieval_status"}]

    async def child_status():
        return {"rag_backend": "lexical", "index_backend": "lexical"}

    monkeypatch.setattr(main.settings, "rag_backend", lambda: "dense")
    monkeypatch.setattr(main, "discover_tools", tools)
    monkeypatch.setattr(main.mcp_client, "retrieval_status", child_status)

    result = asyncio.run(main.health())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert json.loads(result.body) == {
        "status": "misconfigured",
        "mcp_connected": True,
        "rag_status_source": "mcp_child",
        "rag_backend": "lexical",
        "configured_rag_backend": "dense",
        "commit": "unknown",
    }


def test_usage_reports_llm_and_mcp_call_counts():
    usage.reset()
    usage.record_chat_request()
    usage.record_planner("llm")
    usage.record_llm_call()
    usage.record_llm_call()
    usage.record_mcp_call("search_policy_documents")
    usage.record_mcp_call("search_policy_documents")
    usage.record_mcp_call("check_pto_balance")
    usage.record_mcp_discovery()

    body = asyncio.run(main.usage_counters(Response()))

    assert body["chat_requests"] == 1
    assert body["planners"] == {"llm": 1}
    # One question can take several completions, so provider calls are counted
    # per round trip rather than per request.
    assert body["llm"]["provider_calls"] == 2
    assert body["mcp"]["tool_calls"] == 3
    assert body["mcp"]["by_tool"]["search_policy_documents"] == 2
    assert body["mcp"]["schema_discoveries"] == 1


def test_usage_keeps_health_probes_out_of_the_agent_tool_total():
    """A health probe reaches the MCP child. Counting it as agent tool use would
    inflate the demo figure with polling from the host's own monitor."""
    usage.reset()
    usage.record_mcp_call("search_policy_documents")
    usage.record_mcp_call("get_retrieval_status", diagnostic=True)
    usage.record_mcp_call("get_retrieval_status", diagnostic=True)

    body = asyncio.run(main.usage_counters(Response()))

    assert body["mcp"]["tool_calls"] == 1
    assert "get_retrieval_status" not in body["mcp"]["by_tool"]
    assert body["mcp"]["diagnostic_tool_calls"] == 2
    assert body["mcp"]["diagnostic_by_tool"] == {"get_retrieval_status": 2}


def test_usage_response_is_not_cached():
    """Counters change on every request; a cached panel would show stale figures."""
    response = Response()
    asyncio.run(main.usage_counters(response))
    assert response.headers["Cache-Control"] == "no-store"


def test_usage_reports_the_window_the_counters_cover():
    """Per-instance counters are meaningless without the window they cover: a
    woken free-tier instance starts from zero and must not read as 'never used'."""
    usage.reset()
    body = asyncio.run(main.usage_counters(Response()))
    assert body["counters_are_per_instance"] is True
    assert body["process_started_at"]
    assert body["uptime_seconds"] >= 0


def test_usage_reports_provider_token_totals():
    usage.reset()
    usage.record_llm_call(prompt_tokens=1200, completion_tokens=80)
    usage.record_llm_call(prompt_tokens=1500, completion_tokens=140)

    llm = asyncio.run(main.usage_counters(Response()))["llm"]

    assert llm["provider_calls"] == 2
    assert llm["prompt_tokens"] == 2700
    assert llm["completion_tokens"] == 220
    assert llm["total_tokens"] == 2920
    assert llm["calls_with_reported_tokens"] == 2


def test_usage_tracks_how_many_calls_actually_reported_tokens():
    """A provider that omits a usage block would otherwise make the total look
    complete while silently missing that call. The denominator makes it visible."""
    usage.reset()
    usage.record_llm_call(prompt_tokens=900, completion_tokens=60)
    usage.record_llm_call()  # provider returned no usage block

    llm = asyncio.run(main.usage_counters(Response()))["llm"]

    assert llm["provider_calls"] == 2
    assert llm["calls_with_reported_tokens"] == 1
    assert llm["total_tokens"] == 960


def test_tools_marks_which_discovered_tools_the_planner_may_call(monkeypatch):
    """The published annotation must come from the planner's own authorisation
    table, so /tools cannot claim a boundary the planner does not enforce."""
    async def discovered(**_kwargs):
        return [
            {"name": "search_policy_documents", "description": "Retrieve policy.", "inputSchema": {}},
            {"name": "create_mock_hr_ticket", "description": "Draft a ticket.", "inputSchema": {}},
            {"name": "get_retrieval_status", "description": "Child diagnostic.", "inputSchema": {}},
        ]

    monkeypatch.setattr(main, "discover_tools", discovered)

    body = asyncio.run(main.tools())

    by_name = {tool["name"]: tool for tool in body["tools"]}
    assert by_name["search_policy_documents"]["agent_callable"] is True
    assert by_name["search_policy_documents"]["capability"] == "policy_read"
    assert by_name["create_mock_hr_ticket"]["capability"] == "mock_write"
    # The health diagnostic is discovered and published, but never offered.
    assert by_name["get_retrieval_status"]["agent_callable"] is False
    assert by_name["get_retrieval_status"]["capability"] is None
    assert body["agent_callable_count"] == 2


def test_tools_treats_an_unclassified_new_tool_as_not_callable(monkeypatch):
    """A tool added to the MCP server must not become model-callable simply by
    being discovered; it stays unavailable until deliberately classified."""
    async def discovered(**_kwargs):
        return [{"name": "delete_employee_record", "description": "Future.", "inputSchema": {}}]

    monkeypatch.setattr(main, "discover_tools", discovered)

    body = asyncio.run(main.tools())

    assert body["tools"][0]["agent_callable"] is False
    assert body["agent_callable_count"] == 0


def test_marking_starts_a_fresh_window_without_discarding_totals():
    """The demo needs each segment attributable to its own numbers, but a public
    route that zeroed counters would let anyone erase what the instance did."""
    usage.reset()
    usage.record_chat_request()
    usage.record_llm_call(prompt_tokens=500, completion_tokens=40)
    usage.record_mcp_call("search_policy_documents")
    usage.record_mcp_call("check_pto_balance")

    asyncio.run(main.usage_mark(Response()))

    # A fresh window reads zero even though the process has done work.
    after_mark = asyncio.run(main.usage_counters(Response()))
    assert after_mark["chat_requests"] == 0
    assert after_mark["mcp"]["tool_calls"] == 0
    assert after_mark["mcp"]["by_tool"] == {}
    assert after_mark["llm"]["total_tokens"] == 0
    assert after_mark["marked_at"] is not None
    # ...and the record survives the mark.
    assert after_mark["process_totals"]["chat_requests"] == 1
    assert after_mark["process_totals"]["mcp_tool_calls"] == 2
    assert after_mark["process_totals"]["llm_total_tokens"] == 540

    usage.record_mcp_call("search_policy_documents")

    second = asyncio.run(main.usage_counters(Response()))
    assert second["mcp"]["by_tool"] == {"search_policy_documents": 1}
    assert second["process_totals"]["mcp_tool_calls"] == 3


def test_unmarked_usage_reports_the_whole_process_window():
    usage.reset()
    usage.record_chat_request()

    body = asyncio.run(main.usage_counters(Response()))

    assert body["marked_at"] is None
    assert body["measuring_since"] == body["process_started_at"]
    assert body["chat_requests"] == body["process_totals"]["chat_requests"] == 1


def test_health_probes_do_not_count_as_planner_schema_discoveries(monkeypatch):
    """/health discovers schemas on every probe, so a hosted service polls this
    continuously with nobody using the app. A headline that climbs on its own is
    not evidence of agent activity."""
    usage.reset()

    async def discovered(*_args, **_kwargs):
        return [{"name": "search_policy_documents", "description": "", "inputSchema": {}}]

    async def child_status():
        return {"rag_backend": settings.rag_backend(), "index_backend": settings.rag_backend()}

    monkeypatch.setattr(main, "discover_tools", discovered)
    monkeypatch.setattr(main.mcp_client, "retrieval_status", child_status)

    asyncio.run(main.health())
    asyncio.run(main.health())
    asyncio.run(main.tools())

    body = asyncio.run(main.usage_counters(Response()))

    assert body["mcp"]["schema_discoveries"] == 0
    assert body["process_totals"]["mcp_schema_discoveries"] == 0
