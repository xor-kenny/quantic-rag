"""Process-local counters for what this instance actually dispatched.

Two numbers are worth showing in an agentic demo: how often the planner
consulted the LLM, and how often a call crossed the MCP boundary. Both are
counted at the single dispatch site for each -- `planner.respond`'s completion
call and `mcp_client`'s `_invoke` wrappers -- so a counter cannot drift from
the thing it claims to count.

Four deliberate properties:

* **Per-process and in-memory.** A free-tier instance sleeps when idle and wakes
  as a new process with the counters at zero. `snapshot()` therefore reports
  when this process started, so a reader sees the window the counts cover
  instead of mistaking them for lifetime totals.
* **Diagnostic calls counted separately.** `/health` reaches the MCP child on
  every probe. Folding those into the agent total would inflate the demo figure
  with health polling, so they are kept in their own bucket.
* **Marking never subtracts evidence.** Attributing a walkthrough segment to its
  own numbers needs a fresh starting point, but zeroing the counters would let
  anyone erase what the instance had already done. `mark()` records a baseline
  instead: the headline figures become "since the mark" while the running
  process totals stay published beside them, so a mark can narrow attention and
  never destroys the record.
* **Non-fatal.** Counting must never be able to fail a request, so callers wrap
  nothing in try/except -- the operations here cannot raise on valid input.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()

_process_started_at = datetime.now(timezone.utc)
_process_started_monotonic = time.monotonic()


@dataclass
class _Counters:
    """One set of tallies. Two instances subtract to give a windowed view."""

    chat_requests: int = 0
    llm_calls: int = 0
    llm_calls_with_tokens: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_failures: int = 0
    mcp_discoveries: int = 0
    mcp_diagnostic_discoveries: int = 0
    mcp_agent_calls: dict[str, int] = field(default_factory=dict)
    mcp_diagnostic_calls: dict[str, int] = field(default_factory=dict)
    planners: dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> _Counters:
        """Copy deeply enough that a later increment cannot mutate a baseline."""
        return replace(
            self,
            mcp_agent_calls=dict(self.mcp_agent_calls),
            mcp_diagnostic_calls=dict(self.mcp_diagnostic_calls),
            planners=dict(self.planners),
        )

    def since(self, baseline: _Counters) -> _Counters:
        """Return self minus baseline, dropping names that gained nothing."""
        def difference(current: dict[str, int], before: dict[str, int]) -> dict[str, int]:
            counted = {name: total - before.get(name, 0) for name, total in current.items()}
            return {name: count for name, count in counted.items() if count > 0}

        return _Counters(
            chat_requests=self.chat_requests - baseline.chat_requests,
            llm_calls=self.llm_calls - baseline.llm_calls,
            llm_calls_with_tokens=self.llm_calls_with_tokens - baseline.llm_calls_with_tokens,
            llm_prompt_tokens=self.llm_prompt_tokens - baseline.llm_prompt_tokens,
            llm_completion_tokens=self.llm_completion_tokens - baseline.llm_completion_tokens,
            llm_failures=self.llm_failures - baseline.llm_failures,
            mcp_discoveries=self.mcp_discoveries - baseline.mcp_discoveries,
            mcp_diagnostic_discoveries=(
                self.mcp_diagnostic_discoveries - baseline.mcp_diagnostic_discoveries
            ),
            mcp_agent_calls=difference(self.mcp_agent_calls, baseline.mcp_agent_calls),
            mcp_diagnostic_calls=difference(self.mcp_diagnostic_calls, baseline.mcp_diagnostic_calls),
            planners=difference(self.planners, baseline.planners),
        )


_totals = _Counters()
_baseline = _Counters()
_marked_at: datetime | None = None


def record_chat_request() -> None:
    """Count one accepted `POST /chat` (after rate limiting, before planning)."""
    with _lock:
        _totals.chat_requests += 1


def record_planner(name: str) -> None:
    """Count which planner produced a response: llm, deterministic, or a gate."""
    with _lock:
        _totals.planners[name] = _totals.planners.get(name, 0) + 1


def record_llm_call(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Count one chat-completion request sent to the provider, with its tokens.

    The planner loop can send several per user question -- one per tool-use
    round trip -- so this is deliberately not the same as `chat_requests`.

    Token counts are the provider's own reported figures, taken as plain ints so
    this module stays independent of any SDK response type. A provider that
    omits usage contributes nothing, which would silently under-report the
    total, so calls that did report are counted separately and the panel shows
    that denominator rather than implying every call was measured.
    """
    with _lock:
        _totals.llm_calls += 1
        if prompt_tokens or completion_tokens:
            _totals.llm_calls_with_tokens += 1
            _totals.llm_prompt_tokens += prompt_tokens
            _totals.llm_completion_tokens += completion_tokens


def record_llm_failure() -> None:
    """Count one planner run that fell back after the provider failed."""
    with _lock:
        _totals.llm_failures += 1


def record_mcp_discovery(*, diagnostic: bool = False) -> None:
    """Count one `list_tools` schema discovery over the MCP session.

    `/health` discovers schemas on every probe, so a hosted service polls this
    continuously with no user involved. Those are kept out of the headline for
    the same reason diagnostic tool calls are: a figure that climbs on its own
    is not evidence of agent activity.
    """
    with _lock:
        if diagnostic:
            _totals.mcp_diagnostic_discoveries += 1
        else:
            _totals.mcp_discoveries += 1


def record_mcp_call(name: str, *, diagnostic: bool = False) -> None:
    """Count one MCP `call_tool` dispatch, keeping health probes out of the total."""
    with _lock:
        bucket = _totals.mcp_diagnostic_calls if diagnostic else _totals.mcp_agent_calls
        bucket[name] = bucket.get(name, 0) + 1


def mark() -> datetime:
    """Start a fresh measurement window without discarding the running totals.

    Used between walkthrough segments so each one is attributable to its own
    numbers. Because this only moves a baseline, it cannot be used to erase what
    the instance already did -- the process totals remain published.
    """
    global _baseline, _marked_at
    with _lock:
        _baseline = _totals.snapshot()
        _marked_at = datetime.now(timezone.utc)
        return _marked_at


def snapshot() -> dict[str, Any]:
    """Return the counters plus the window they cover. Safe to expose publicly."""
    with _lock:
        windowed = _totals.since(_baseline)
        totals = _totals.snapshot()
        marked_at = _marked_at

    agent_calls = dict(sorted(windowed.mcp_agent_calls.items(), key=lambda item: -item[1]))
    diagnostic_calls = dict(sorted(windowed.mcp_diagnostic_calls.items(), key=lambda item: -item[1]))
    return {
        "process_started_at": _process_started_at.isoformat(timespec="seconds"),
        "uptime_seconds": round(time.monotonic() - _process_started_monotonic, 1),
        "counters_are_per_instance": True,
        # Headline figures cover the current window: the whole process, or the
        # period since the last mark.
        "measuring_since": (marked_at or _process_started_at).isoformat(timespec="seconds"),
        "marked_at": marked_at.isoformat(timespec="seconds") if marked_at else None,
        "chat_requests": windowed.chat_requests,
        "planners": dict(sorted(windowed.planners.items(), key=lambda item: -item[1])),
        "llm": {
            "provider_calls": windowed.llm_calls,
            "provider_failures": windowed.llm_failures,
            # Provider-reported figures. `calls_with_reported_tokens` is the
            # denominator: totals cover only those calls, never an estimate
            # for the rest.
            "calls_with_reported_tokens": windowed.llm_calls_with_tokens,
            "prompt_tokens": windowed.llm_prompt_tokens,
            "completion_tokens": windowed.llm_completion_tokens,
            "total_tokens": windowed.llm_prompt_tokens + windowed.llm_completion_tokens,
        },
        "mcp": {
            "tool_calls": sum(agent_calls.values()),
            "by_tool": agent_calls,
            "schema_discoveries": windowed.mcp_discoveries,
            "diagnostic_schema_discoveries": windowed.mcp_diagnostic_discoveries,
            "diagnostic_tool_calls": sum(diagnostic_calls.values()),
            "diagnostic_by_tool": diagnostic_calls,
        },
        # Published unconditionally so a mark narrows attention rather than
        # hiding what this process has done since it started.
        "process_totals": {
            "chat_requests": totals.chat_requests,
            "llm_provider_calls": totals.llm_calls,
            "llm_total_tokens": totals.llm_prompt_tokens + totals.llm_completion_tokens,
            "mcp_tool_calls": sum(totals.mcp_agent_calls.values()),
            "mcp_diagnostic_tool_calls": sum(totals.mcp_diagnostic_calls.values()),
            "mcp_schema_discoveries": totals.mcp_discoveries,
            "mcp_diagnostic_schema_discoveries": totals.mcp_diagnostic_discoveries,
        },
    }


def reset() -> None:
    """Clear every counter and any mark. Used by tests; no HTTP route calls it."""
    global _totals, _baseline, _marked_at
    with _lock:
        _totals = _Counters()
        _baseline = _Counters()
        _marked_at = None
