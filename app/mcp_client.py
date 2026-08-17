"""The agent's MCP client.

Every production tool call uses the MCP stdio transport. The app launches the
same FastMCP server that `scripts/mcp_check.py` validates in CI, negotiates the
MCP protocol, then calls a discovered tool by name. Keeping the server in the
same deployed service is free-tier friendly; it does not bypass the protocol.

Two deployment properties drive the shape of this module:

**One server process, not one per request.** Spawning a fresh interpreter per
call measured at roughly 590 ms and 51 MB. On a small container that means the
health probe forks a Python process on every check and a handful of concurrent
requests can exhaust memory. A single long-lived session keeps the protocol
boundary intact and removes both problems.

**The session is owned by a dedicated task.** `stdio_client` and `ClientSession`
are anyio context managers whose cancel scopes must be exited in the task that
entered them. Holding them open across an application lifetime therefore cannot
be done with a shared `AsyncExitStack` touched from request handlers — closing
from another task raises "Attempted to exit cancel scope in a different task".
A supervisor task enters both managers, publishes the session, and waits for a
stop signal, so entry and exit always happen on the same task.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from . import settings, usage

logger = logging.getLogger(__name__)


def _server_environment() -> dict[str, str]:
    """Forward the retrieval settings the MCP child needs, and nothing more.

    The SDK does not inherit the parent environment. `get_default_environment()`
    returns a minimal safe set (HOME, LOGNAME, PATH, SHELL, USER), so a host
    variable such as `RAG_BACKEND` never reaches the child unless it is passed
    explicitly. Retrieval runs *in the child*, so without this the parent can
    report one backend on /health while the child serves another — the service
    silently answers from the lexical index while the deployment claims dense.

    The allow-list is deliberate rather than a copy of `os.environ`: the child
    reads policy and synthetic records only, and has no use for the provider
    credential. Not forwarding `OPENAI_API_KEY` keeps the key in the one process
    that needs it.
    """
    environment = dict(get_default_environment())
    for name in ("RAG_BACKEND", "RAG_MODEL", "RAG_MODEL_CACHE_DIR", "TOP_K", "MIN_SUPPORT"):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


# `cwd` is pinned to the repository root rather than inherited. `python -m` puts
# the working directory on sys.path, so inheriting a different start directory on
# a host would make `app.mcp_server` unimportable and the server fail to launch.
_SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "app.mcp_server"],
    cwd=str(settings.ROOT),
    env=_server_environment(),
)

_task: asyncio.Task[None] | None = None
_session: ClientSession | None = None
_ready: asyncio.Event | None = None
_stop: asyncio.Event | None = None
_error: BaseException | None = None
_lock = asyncio.Lock()


async def _supervise(ready: asyncio.Event, stop: asyncio.Event) -> None:
    """Own the MCP session for its whole lifetime, on one task."""
    global _session, _error
    try:
        async with stdio_client(_SERVER) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                _session, _error = session, None
                logger.info("MCP stdio session established")
                ready.set()
                await stop.wait()
    except BaseException as exc:  # publish the failure instead of dying silently
        _error = exc
        raise
    finally:
        _session = None
        ready.set()


async def _start() -> None:
    global _task, _ready, _stop, _error
    ready, stop = asyncio.Event(), asyncio.Event()
    _ready, _stop, _error = ready, stop, None
    _task = asyncio.create_task(_supervise(ready, stop), name="mcp-session")
    await ready.wait()
    if _session is None:
        raise RuntimeError(f"MCP server failed to start: {_error!r}")


async def _stop_task() -> None:
    global _task, _session
    if _task is None:
        return
    if _stop is not None:
        _stop.set()
    task = _task
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=settings.MCP_SHUTDOWN_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        # Give the supervisor task a chance to close its stdio child process.
        # Suppressing cancellation here is intentional: shutdown must finish even
        # when a broken server ignored the cooperative stop signal.
        with suppress(asyncio.CancelledError):
            await task
    except BaseException as exc:  # teardown must not propagate
        logger.warning("MCP session teardown failed: %s", exc)
    _task, _session = None, None


def _alive() -> bool:
    return _session is not None and _task is not None and not _task.done()


async def session() -> ClientSession:
    """Return the shared session, starting or restarting the server if needed."""
    if _alive():
        return _session  # type: ignore[return-value]
    async with _lock:
        if not _alive():
            await _stop_task()
            await _start()
        return _session  # type: ignore[return-value]


async def startup() -> None:
    """Open the session eagerly so a broken MCP server fails fast at boot."""
    await session()


async def shutdown() -> None:
    """Terminate the MCP server subprocess with the application."""
    async with _lock:
        await _stop_task()


@asynccontextmanager
async def request_session() -> AsyncIterator[None]:
    """Ensure a live MCP session for the duration of one agent request.

    Retained as the call-site API. It no longer spawns a process — the shared
    session already exists — but it keeps the guarantee that every tool call in
    a single request travels over one initialised MCP connection.
    """
    await session()
    yield


async def _invoke(operation: str, *args: Any) -> Any:
    """Run one session method, reconnecting once if the child process has died."""
    active = await session()
    try:
        return await asyncio.wait_for(
            getattr(active, operation)(*args), timeout=settings.MCP_OPERATION_TIMEOUT_SECONDS
        )
    except Exception as exc:
        logger.warning("MCP %s failed (%s); reconnecting", operation, exc)
        async with _lock:
            await _stop_task()
        active = await session()
        return await asyncio.wait_for(
            getattr(active, operation)(*args), timeout=settings.MCP_OPERATION_TIMEOUT_SECONDS
        )


def _payload(result: Any) -> Any:
    """Read structured MCP output first, with text JSON as a compatibility fallback."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured.get("result", structured)
    text = "".join(block.text for block in result.content if hasattr(block, "text"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "is_error": bool(getattr(result, "isError", False))}


async def discover_tools(*, diagnostic: bool = False) -> list[dict[str, Any]]:
    """Discover schemas from the running MCP server; schemas are never hard-coded.

    `diagnostic` marks a discovery no agent asked for -- the health probe and the
    dashboard's own schema panel -- so continuous host polling does not read as
    planner activity.
    """
    listed = await _invoke("list_tools")
    usage.record_mcp_discovery(diagnostic=diagnostic)
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
        for tool in listed.tools
    ]


async def call(name: str, arguments: dict[str, Any], *, diagnostic: bool = False) -> Any:
    """Call a named MCP tool over stdio and return its structured result.

    `diagnostic` marks a call the agent did not make -- currently only the
    health probe -- so the usage panel does not report health polling as agent
    tool use. Counting happens after the call returns, so the figure is
    completed calls rather than attempts.
    """
    result = _payload(await _invoke("call_tool", name, arguments))
    usage.record_mcp_call(name, diagnostic=diagnostic)
    return result


async def retrieval_status() -> dict[str, Any]:
    """Return the effective retrieval configuration from the MCP child.

    Do not substitute a parent-process environment variable here: this call is
    deliberately how `/health` proves that the process serving RAG loaded the
    claimed backend. The result contains only operational metadata, never a
    provider credential or policy/employee content.
    """
    status = await call("get_retrieval_status", {}, diagnostic=True)
    if not isinstance(status, dict):
        raise RuntimeError("MCP retrieval status was not an object")
    backend = status.get("rag_backend")
    if backend not in {"lexical", "dense"} or status.get("index_backend") != backend:
        raise RuntimeError("MCP retrieval status had an invalid backend")
    if backend == "dense" and status.get("dense_encoder_loaded") is not True:
        raise RuntimeError("MCP dense retrieval encoder is not ready")
    return status
