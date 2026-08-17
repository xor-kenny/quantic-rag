"""Verify MCP tool discovery and a live tool call over the stdio transport.

The deployed service likewise calls this server over stdio, but this script
verifies that boundary independently. It launches `python -m app.mcp_server` as
a separate process, speaks MCP to it over stdio, lists the tools, confirms
child-side retrieval status, and calls two real data/policy tools — the
evidence the project brief asks CI to provide.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = {
    "search_policy_documents",
    "get_retrieval_status",
    "get_policy_section",
    "lookup_employee_profile",
    "check_pto_balance",
    "lookup_benefits_status",
    "create_mock_hr_ticket",
}


def _child_environment() -> dict[str, str]:
    """Pass non-secret retrieval settings to the independently spawned child.

    The MCP SDK starts a tool child with a minimal safe environment. This check
    must preserve `RAG_BACKEND` and related retrieval settings or a command
    such as `RAG_BACKEND=dense python scripts/mcp_check.py` would falsely test
    the lexical default. As in the production client, the OpenAI credential is
    deliberately not forwarded because this process never calls the provider.
    """
    environment = dict(get_default_environment())
    for name in ("RAG_BACKEND", "RAG_MODEL", "RAG_MODEL_CACHE_DIR", "TOP_K", "MIN_SUPPORT"):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _payload(result) -> object:
    """Prefer the structured result; fall back to parsing the text content."""
    if getattr(result, "structuredContent", None):
        structured = result.structuredContent
        return structured.get("result", structured)
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def main() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(ROOT),
        env=_child_environment(),
    )

    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            missing = REQUIRED - names
            if missing:
                raise SystemExit(f"MCP server is missing required tools: {sorted(missing)}")
            print(f"Discovered {len(names)} MCP tools over stdio: {sorted(names)}")

            status = _payload(await session.call_tool("get_retrieval_status", {}))
            if not isinstance(status, dict) or status.get("rag_backend") != status.get("index_backend"):
                raise SystemExit(f"get_retrieval_status returned an unexpected payload: {status!r}")
            if status["rag_backend"] == "dense" and status.get("dense_encoder_loaded") is not True:
                raise SystemExit("get_retrieval_status reported dense without a warmed encoder")
            print(
                "get_retrieval_status confirmed "
                f"{status['rag_backend']} retrieval from the MCP child"
            )

            policy = _payload(await session.call_tool(
                "search_policy_documents", {"query": "PTO notice period", "limit": 2}
            ))
            if not isinstance(policy, list) or not policy:
                raise SystemExit(f"search_policy_documents returned no evidence: {policy!r}")
            print(f"search_policy_documents returned {len(policy)} chunks; top document: {policy[0]['document']}")

            balance = _payload(await session.call_tool("check_pto_balance", {"employee_id": "E1001"}))
            if not isinstance(balance, dict) or "available_hours" not in balance:
                raise SystemExit(f"check_pto_balance returned an unexpected payload: {balance!r}")
            print(f"check_pto_balance returned {balance['available_hours']} hours for E1001")

    print("MCP discovery and tool-call check passed")


if __name__ == "__main__":
    asyncio.run(main())
