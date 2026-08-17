"""Run the evaluation set against ClearHR and report measured metrics.

Usage:
    python -m evaluation.run_eval
    python -m evaluation.run_eval --ablation
    python -m evaluation.run_eval --base-url https://your-clearhr-service.example
    python -m evaluation.run_eval --base-url https://your-clearhr-service.example --runs 3 \
        --require-rag-backend dense --deployment-revision <sha>
    python -m evaluation.run_eval --out results.md

Local mode exercises the installed agent and its deterministic fallback without
requiring an API key. ``--base-url`` instead POSTs the same cases to a deployed
``/chat`` endpoint, so its latency and HTTP availability are measured from the
client rather than inferred from a local function call.

Every number printed is computed from a response produced during the run. The
answer check is deterministic: each case has a human-authored gold reference
and a small, explicit rubric of required claims. It is intentionally not
presented as a replacement for human groundedness review.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app import mcp_client
from app.agent import respond
from app.rag import build_index, load_index, search

SET_PATH = Path(__file__).parent / "evaluation_set.json"
HTTP_TIMEOUT_SECONDS = 45.0

# How each expected_behavior is detected on the response payload.
REFUSAL_KEYS = ("out_of_corpus", "refused")
CLARIFY_KEYS = ("needs_clarification",)

# Common connective words make a reference-answer overlap score look healthier
# than it is. Keep policy terms, numbers, IDs, and named entities instead.
REFERENCE_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "before", "by", "can", "do", "does",
    "for", "from", "how", "i", "if", "in", "is", "it", "its", "me", "my", "not", "of",
    "on", "or", "our", "should", "so", "that", "the", "their", "this", "to", "what",
    "when", "which", "who", "with", "you", "your",
}


def load_cases() -> list[dict[str, Any]]:
    """Load the versioned evaluation set without changing it."""
    return json.loads(SET_PATH.read_text(encoding="utf-8"))


def _normalise_text(value: object) -> str:
    """Make phrase checks case- and punctuation-insensitive."""
    text = unicodedata.normalize("NFKD", str(value)).lower()
    text = text.replace("’", "'").replace("-", " ")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _reference_terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]{2,}", _normalise_text(value))
        if term not in REFERENCE_STOP_WORDS
    }


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _required_groups(case: dict[str, Any]) -> list[list[str]]:
    """Return one alternative group for every required answer claim.

    A rubric entry such as ``[["five calendar days", "5 calendar days"],
    ["manager approval"]]`` means the answer must mention either form of the
    first fact *and* the manager-approval fact. A full reference-answer phrase
    is used as a conservative fallback for ad-hoc cases that omit a rubric.
    """
    rubric = case.get("rubric") or {}
    raw_groups = rubric.get("required")
    if raw_groups is None:
        expected = str(case.get("expected", "")).strip()
        return [[expected]] if expected else []

    groups: list[list[str]] = []
    for raw_group in raw_groups:
        alternatives = raw_group if isinstance(raw_group, list) else [raw_group]
        group = [str(item).strip() for item in alternatives if str(item).strip()]
        if group:
            groups.append(group)
    return groups


def score_answer_content(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Score explicit claims from the gold answer and its deterministic rubric.

    The rubric protects against a generic answer receiving full credit just
    because it cites the expected policy. Reference-term recall is a secondary
    signal derived directly from ``expected``; its threshold is deliberately
    configured per case because a correct concise answer need not reproduce a
    whole reference paragraph verbatim.
    """
    expected = str(case.get("expected", "")).strip()
    answer = str(result.get("answer") or "")
    answer_normalised = _normalise_text(answer)

    groups = _required_groups(case)
    matched_groups: list[list[str]] = []
    missing_groups: list[list[str]] = []
    for group in groups:
        if any(_normalise_text(option) in answer_normalised for option in group):
            matched_groups.append(group)
        else:
            missing_groups.append(group)

    rubric = case.get("rubric") or {}
    prohibited = [str(item) for item in rubric.get("prohibited", []) if str(item).strip()]
    prohibited_hits = [
        phrase for phrase in prohibited if _normalise_text(phrase) in answer_normalised
    ]

    reference_terms = _reference_terms(expected)
    answer_terms = _reference_terms(answer)
    matched_reference_terms = reference_terms & answer_terms
    reference_term_recall = _safe_ratio(len(matched_reference_terms), len(reference_terms))
    # A low default protects against a generic answer passing only because it
    # happened to hit a short rubric phrase. Cases can tighten or relax this
    # explicitly when their gold answer is unusually terse or verbose.
    min_reference_recall = float(rubric.get("minimum_reference_term_recall", 0.15))
    reference_ok = bool(expected) and reference_term_recall >= min_reference_recall
    rubric_ok = not missing_groups and not prohibited_hits

    return {
        "expected_answer": expected,
        "answer_content_ok": bool(answer.strip()) and rubric_ok and reference_ok,
        "rubric_ok": rubric_ok,
        "required_claims_matched": len(matched_groups),
        "required_claims_total": len(groups),
        "missing_claims": missing_groups,
        "prohibited_hits": prohibited_hits,
        "reference_term_recall": reference_term_recall,
        "reference_terms_matched": len(matched_reference_terms),
        "reference_terms_total": len(reference_terms),
        "minimum_reference_term_recall": min_reference_recall,
    }


def behaviour_of(result: dict[str, Any]) -> str:
    if result.get("_http_error"):
        return "http_error"
    # A confirmed sensitive report remains an escalation, but it is also the
    # positive action workflow. Give the action outcome precedence so q29 can
    # prove confirmation creates a synthetic ticket.
    if result.get("mock_action"):
        return "action_taken"
    if result.get("escalation"):
        return "escalate"
    if any(result.get(key) for key in REFUSAL_KEYS):
        return "refuse_out_of_scope"
    if any(result.get(key) for key in CLARIFY_KEYS):
        return "request_clarification"
    return "answer"


def behaviour_matches(expected: str, observed: str, result: dict[str, Any]) -> bool:
    if expected in {"answer_with_citation", "answer_with_citation_and_record", "answer_from_record"}:
        return observed == "answer"
    if expected == "require_confirmation":
        # Text-level confirmation requirements are separately checked by the
        # case rubric; this check only guarantees no ticket was created first.
        return observed != "action_taken"
    return expected == observed


def _coverage(expected: set[str], observed: set[str]) -> dict[str, Any]:
    """Return strict required-set coverage plus transparent precision/recall."""
    matched = expected & observed
    if not expected:
        return {
            "matched": matched,
            "recall": 1.0 if not observed else 0.0,
            "precision": 1.0 if not observed else 0.0,
            "complete": not observed,
        }
    return {
        "matched": matched,
        "recall": _safe_ratio(len(matched), len(expected)),
        "precision": _safe_ratio(len(matched), len(observed)),
        "complete": expected.issubset(observed),
    }


def _citation_shape_ok(citations: list[dict[str, Any]]) -> bool:
    return all(
        isinstance(citation, dict)
        and isinstance(citation.get("id"), str)
        and isinstance(citation.get("document"), str)
        and isinstance(citation.get("section"), str)
        and isinstance(citation.get("snippet"), str)
        for citation in citations
    )


def score_case(
    case: dict[str, Any], result: dict[str, Any], index_ids: set[str] | None
) -> dict[str, Any]:
    """Score one response. ``index_ids=None`` is used in deployed HTTP mode."""
    trace = [step for step in result.get("trace", []) if isinstance(step, dict)]
    tools_called = [str(step.get("tool")) for step in trace if step.get("tool")]
    citations = [citation for citation in result.get("citations", []) if isinstance(citation, dict)]
    cited_documents = {str(citation["document"]) for citation in citations if citation.get("document")}
    expected_documents = {str(document) for document in case.get("expected_documents") or []}
    expected_tools = {str(tool) for tool in case.get("expected_tools") or []}

    document_coverage = _coverage(expected_documents, cited_documents)
    tool_coverage = _coverage(expected_tools, set(tools_called))
    citation_ids = [str(citation["id"]) for citation in citations if citation.get("id")]
    citation_shape_ok = _citation_shape_ok(citations)
    # The remote service does not expose its full index. Do not falsely claim
    # local IDs prove a deployed build's citations resolve.
    citations_resolve = (
        all(citation_id in index_ids for citation_id in citation_ids)
        if index_ids is not None
        else None
    )
    # An empty expected-document list means "no particular document is
    # required," not "the answer must contain no evidence." For example, a
    # safe foreign-jurisdiction refusal deliberately calls the policy search
    # tool and may cite the returned US-only scope evidence. Keep structure and
    # local-ID checks strict while allowing that grounded refusal to pass.
    citation_ok = (
        (not expected_documents or document_coverage["complete"])
        and citation_shape_ok
        and citations_resolve is not False
    )

    observed = behaviour_of(result)
    behaviour_ok = behaviour_matches(case["expected_behavior"], observed, result)
    action_expected = case["expected_behavior"] == "action_taken"
    action_created = result.get("mock_action") is not None
    action_ok = (
        bool(case.get("confirm_mock_action"))
        and action_created
        and "create_mock_hr_ticket" in tools_called
        if action_expected
        else not action_created
    )
    answer_score = score_answer_content(case, result)

    # Groundedness remains a proxy, but now requires every document declared by
    # the case (not merely one document from a multi-policy answer).
    grounded_proxy = not expected_documents or citation_ok
    overall_ok = (
        behaviour_ok
        and answer_score["answer_content_ok"]
        and citation_ok
        and tool_coverage["complete"]
        and action_ok
        and not result.get("_http_error")
    )

    return {
        "id": case["id"],
        "type": case["type"],
        "question": case["question"],
        "expected_behavior": case["expected_behavior"],
        "observed_behavior": observed,
        "behaviour_ok": behaviour_ok,
        "expected_documents": sorted(expected_documents),
        "cited_documents": sorted(cited_documents),
        "citation_document_recall": document_coverage["recall"],
        "citation_document_precision": document_coverage["precision"],
        "citation_documents_matched": len(document_coverage["matched"]),
        "citation_documents_expected": len(expected_documents),
        "citation_documents_cited": len(cited_documents),
        "citation_coverage_ok": document_coverage["complete"],
        "citation_shape_ok": citation_shape_ok,
        "citation_ok": citation_ok,
        "citations_resolve": citations_resolve,
        "expected_tools": sorted(expected_tools),
        "tools_called": tools_called,
        "tool_required_recall": tool_coverage["recall"],
        "tool_required_coverage_ok": tool_coverage["complete"],
        "tool_ok": tool_coverage["complete"],
        "action_expected": action_expected,
        "action_created": action_created,
        "action_ok": action_ok,
        "grounded_proxy": grounded_proxy,
        "overall_ok": overall_ok,
        "http_status": result.get("_http_status"),
        "http_ok": (
            isinstance(result.get("_http_status"), int)
            and 200 <= result["_http_status"] < 300
        ),
        "http_error": result.get("_http_error"),
        "latency_ms": float(result["_latency_ms"]),
        "planner": result.get("planner", "unknown"),
        # These artifacts are generated only from the synthetic evaluation set.
        # Keeping the real response evidence beside its score makes the
        # automatic rubric auditable by a human without exposing app traffic.
        "answer": str(result.get("answer") or ""),
        "citations": citations,
        "trace": trace,
        "mock_action": result.get("mock_action"),
        **answer_score,
    }


async def run_local_once(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run locally and explicitly close the shared MCP child at the end."""
    index_ids = {chunk["id"] for chunk in load_index()["chunks"]}
    rows = []
    await mcp_client.startup()
    try:
        for case in cases:
            started = time.perf_counter()
            result = await respond(
                case["question"],
                case.get("employee_id"),
                confirm_action=bool(case.get("confirm_mock_action")),
            )
            result["_latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            rows.append(score_case(case, result, index_ids))
    finally:
        await mcp_client.shutdown()
    return rows


async def run_http_once(cases: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    """Run cases through a deployed service and retain actual client latency."""
    rows = []
    url = base_url.rstrip("/")
    timeout = httpx.Timeout(HTTP_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(base_url=url, timeout=timeout) as client:
        for case in cases:
            payload = {
                "message": case["question"],
                "employee_id": case.get("employee_id"),
                "confirm_mock_action": bool(case.get("confirm_mock_action")),
            }
            started = time.perf_counter()
            result: dict[str, Any]
            try:
                response = await client.post("/chat", json=payload)
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                decoded = response.json()
                result = decoded if isinstance(decoded, dict) else {}
                result["_http_status"] = response.status_code
                if not response.is_success:
                    result["_http_error"] = f"HTTP {response.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                result = {"_http_error": f"{type(exc).__name__}: {exc}"[:240]}
            result["_latency_ms"] = latency_ms
            rows.append(score_case(case, result, index_ids=None))
    return rows


async def run_once(cases: list[dict[str, Any]], base_url: str | None = None) -> list[dict[str, Any]]:
    """Select the local or deployed evaluator without changing case semantics."""
    if base_url:
        return await run_http_once(cases, base_url)
    return await run_local_once(cases)


def _rate_details(rows: list[dict[str, Any]], key: str) -> dict[str, int | float | None]:
    """Return raw count/fraction data suitable for repeated-run aggregation."""
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return {"passed": None, "total": 0, "fraction": None}
    passed = sum(bool(value) for value in values)
    total = len(values)
    return {"passed": passed, "total": total, "fraction": passed / total}


def _format_rate(details: dict[str, int | float | None]) -> str:
    fraction = details["fraction"]
    if fraction is None:
        return "n/a"
    return f"{details['passed']}/{details['total']} ({float(fraction):.0%})"


def _rate(rows: list[dict[str, Any]], key: str) -> str:
    """Render a raw rate for the existing single-run report format."""
    return _format_rate(_rate_details(rows, key))


def _micro_document_details(
    rows: list[dict[str, Any]], metric: str
) -> dict[str, int | float | None]:
    graded = [row for row in rows if row["citation_documents_expected"]]
    if not graded:
        return {"passed": None, "total": 0, "fraction": None}
    matched = sum(row["citation_documents_matched"] for row in graded)
    if metric == "recall":
        denominator = sum(row["citation_documents_expected"] for row in graded)
    else:
        denominator = sum(row["citation_documents_cited"] for row in graded)
    return {
        "passed": matched,
        "total": denominator,
        "fraction": matched / denominator if denominator else None,
    }


def _micro_document_metric(rows: list[dict[str, Any]], metric: str) -> str:
    return _format_rate(_micro_document_details(rows, metric))


def _micro_tool_recall_details(rows: list[dict[str, Any]]) -> dict[str, int | float | None]:
    graded = [row for row in rows if row["expected_tools"]]
    if not graded:
        return {"passed": None, "total": 0, "fraction": None}
    expected = sum(len(row["expected_tools"]) for row in graded)
    matched = sum(round(row["tool_required_recall"] * len(row["expected_tools"])) for row in graded)
    return {"passed": matched, "total": expected, "fraction": matched / expected}


def _micro_tool_recall(rows: list[dict[str, Any]]) -> str:
    return _format_rate(_micro_tool_recall_details(rows))


def summarise(rows: list[dict[str, Any]], base_url: str | None = None) -> dict[str, Any]:
    latencies = sorted(row["latency_ms"] for row in rows)
    workflows = [row for row in rows if row["expected_behavior"].startswith("answer")]
    citation_cases = [row for row in rows if row["citation_documents_expected"]]
    multi_document_cases = [row for row in citation_cases if row["citation_documents_expected"] > 1]
    confirmed_actions = [row for row in rows if row["action_expected"]]
    planners = {row["planner"] for row in rows}

    metric_details = {
        "behaviour_accuracy": _rate_details(rows, "behaviour_ok"),
        "answer_rubric_accuracy": _rate_details(rows, "answer_content_ok"),
        "end_to_end_pass_rate": _rate_details(rows, "overall_ok"),
        "citation_document_recall": _micro_document_details(rows, "recall"),
        "citation_document_precision": _micro_document_details(rows, "precision"),
        "citation_complete_coverage": _rate_details(citation_cases, "citation_coverage_ok"),
        "multi_document_complete_coverage": _rate_details(
            multi_document_cases, "citation_coverage_ok"
        ),
        "citation_shape": _rate_details(rows, "citation_shape_ok"),
        "citations_resolve": _rate_details(rows, "citations_resolve"),
        "tool_required_recall": _micro_tool_recall_details(rows),
        "tool_required_coverage": _rate_details(rows, "tool_required_coverage_ok"),
        "workflow_completion": _rate_details(workflows, "overall_ok"),
        "action_confirmation_contract": _rate_details(rows, "action_ok"),
        "confirmed_action_completion": _rate_details(confirmed_actions, "action_ok"),
        "groundedness_proxy": _rate_details(rows, "grounded_proxy"),
        "http_success": _rate_details(rows, "http_ok") if base_url else {
            "passed": None, "total": 0, "fraction": None,
        },
    }

    return {
        "mode": "http" if base_url else "local",
        "base_url": base_url,
        "cases": len(rows),
        "planner": next(iter(planners)) if len(planners) == 1 else "mixed",
        "behaviour_accuracy": _format_rate(metric_details["behaviour_accuracy"]),
        "answer_rubric_accuracy": _format_rate(metric_details["answer_rubric_accuracy"]),
        "end_to_end_pass_rate": _format_rate(metric_details["end_to_end_pass_rate"]),
        "citation_document_recall": _format_rate(metric_details["citation_document_recall"]),
        "citation_document_precision": _format_rate(metric_details["citation_document_precision"]),
        "citation_complete_coverage": _format_rate(metric_details["citation_complete_coverage"]),
        "multi_document_complete_coverage": _format_rate(
            metric_details["multi_document_complete_coverage"]
        ),
        "citation_shape": _format_rate(metric_details["citation_shape"]),
        "citations_resolve": _format_rate(metric_details["citations_resolve"]),
        "tool_required_recall": _format_rate(metric_details["tool_required_recall"]),
        "tool_required_coverage": _format_rate(metric_details["tool_required_coverage"]),
        "workflow_completion": _format_rate(metric_details["workflow_completion"]),
        "action_confirmation_contract": _format_rate(
            metric_details["action_confirmation_contract"]
        ),
        "confirmed_action_completion": _format_rate(
            metric_details["confirmed_action_completion"]
        ),
        "groundedness_proxy": _format_rate(metric_details["groundedness_proxy"]),
        "http_success": _format_rate(metric_details["http_success"]),
        "latency_p50_ms": statistics.median(latencies) if latencies else 0,
        "latency_p95_ms": latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0,
        "metric_details": metric_details,
    }


RATE_METRICS = (
    ("Behaviour accuracy", "behaviour_accuracy"),
    ("Answer rubric accuracy", "answer_rubric_accuracy"),
    ("End-to-end pass rate", "end_to_end_pass_rate"),
    ("Citation document recall", "citation_document_recall"),
    ("Citation document precision", "citation_document_precision"),
    ("Citation complete required coverage", "citation_complete_coverage"),
    ("Multi-document complete coverage", "multi_document_complete_coverage"),
    ("Citation structure valid", "citation_shape"),
    ("Citation IDs resolve to local index", "citations_resolve"),
    ("Required-tool recall", "tool_required_recall"),
    ("Required-tool complete coverage", "tool_required_coverage"),
    ("Workflow completion", "workflow_completion"),
    ("Confirmation/action contract", "action_confirmation_contract"),
    ("Confirmed mock-action completion", "confirmed_action_completion"),
    ("Groundedness (automatic proxy)", "groundedness_proxy"),
    ("HTTP success", "http_success"),
)
LATENCY_METRICS = (("Latency p50", "latency_p50_ms"), ("Latency p95", "latency_p95_ms"))


def _utc_timestamp() -> str:
    """Return a stable, explicit UTC timestamp for evaluation provenance."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _evaluator_git_sha() -> str:
    """Capture the local evaluator revision when this runs from a Git checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=SET_PATH.parent.parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def evaluation_set_sha256() -> str:
    """Identify the exact versioned questions used in an evaluation run."""
    return hashlib.sha256(SET_PATH.read_bytes()).hexdigest()


async def fetch_deployment_health(base_url: str) -> dict[str, Any]:
    """Read non-secret deployment facts before a billable HTTP evaluation.

    `/health` now asks the MCP child for its status. This preserves the actual
    child backend/model alongside the evaluation instead of inferring it from a
    retrieval-score range or from a web-process environment variable.
    """
    url = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS)) as client:
            response = await client.get("/health")
            decoded = response.json()
        return {
            "http_status": response.status_code,
            "payload": decoded if isinstance(decoded, dict) else {"raw": str(decoded)[:240]},
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"http_status": None, "error": f"{type(exc).__name__}: {exc}"[:240]}


def _require_expected_backend(health: dict[str, Any], expected_backend: str) -> None:
    """Fail before the 29 requests if child-side deployment evidence is wrong."""
    payload = health.get("payload")
    observed = payload.get("rag_backend") if isinstance(payload, dict) else None
    source = payload.get("rag_status_source") if isinstance(payload, dict) else None
    if health.get("http_status") != 200 or observed != expected_backend or source != "mcp_child":
        raise RuntimeError(
            "Deployment health did not verify the requested MCP-child backend "
            f"{expected_backend!r}; observed HTTP {health.get('http_status')}, backend {observed!r}, "
            f"status source {source!r}."
        )


def build_provenance(
    *,
    base_url: str | None,
    run_count: int,
    deployment_revision: str | None,
    deployment_model: str | None,
    expected_backend: str | None,
    deployment_health: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build auditable, non-secret metadata for a repeated evaluation artifact."""
    return {
        "mode": "http" if base_url else "local",
        "base_url": base_url,
        "run_count": run_count,
        "evaluation_set_sha256": evaluation_set_sha256(),
        "evaluator_git_sha": _evaluator_git_sha(),
        "requested_deployment_revision": deployment_revision or "not supplied",
        "requested_deployment_model": deployment_model or "not supplied",
        "required_mcp_child_backend": expected_backend or "not required",
        "deployment_health": deployment_health,
    }


async def run_repeated(
    cases: list[dict[str, Any]], runs: int, base_url: str | None = None
) -> list[dict[str, Any]]:
    """Run full evaluations sequentially and retain every response set.

    Sequential execution is intentional: parallel calls would contend for a
    small free-tier service and can distort latency, tool ordering, and rate
    limits. The returned records are kept intact for artifacts rather than
    cherry-picking a strongest run.
    """
    if runs < 1:
        raise ValueError("runs must be at least 1")

    records: list[dict[str, Any]] = []
    for run_number in range(1, runs + 1):
        started_at_utc = _utc_timestamp()
        rows = await run_once(cases, base_url=base_url)
        finished_at_utc = _utc_timestamp()
        records.append({
            "run_number": run_number,
            "started_at_utc": started_at_utc,
            "finished_at_utc": finished_at_utc,
            "rows": rows,
            "summary": summarise(rows, base_url=base_url),
        })
    return records


def aggregate_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate median and min/max across full runs, never a best-run score."""
    if not summaries:
        raise ValueError("at least one evaluation summary is required")

    metrics: list[dict[str, Any]] = []
    for label, key in RATE_METRICS:
        details = [summary["metric_details"][key] for summary in summaries]
        values = [float(detail["fraction"]) for detail in details if detail["fraction"] is not None]
        metrics.append({
            "label": label,
            "key": key,
            "unit": "rate",
            "median": statistics.median(values) if values else None,
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "individual": [summary[key] for summary in summaries],
        })

    for label, key in LATENCY_METRICS:
        values = [float(summary[key]) for summary in summaries]
        metrics.append({
            "label": label,
            "key": key,
            "unit": "ms",
            "median": statistics.median(values),
            "minimum": min(values),
            "maximum": max(values),
            "individual": [f"{value:.1f} ms" for value in values],
        })

    return {"run_count": len(summaries), "metrics": metrics}


def _render_provenance(provenance: dict[str, Any]) -> list[str]:
    """Render the safe facts needed to reproduce or challenge an evaluation."""
    lines = ["## Run provenance", ""]
    lines += [
        f"- Evaluation set SHA-256: `{provenance['evaluation_set_sha256']}`",
        f"- Evaluator Git SHA: `{provenance['evaluator_git_sha']}`",
        f"- Requested deployment revision: `{provenance['requested_deployment_revision']}`",
        f"- Requested deployment model: `{provenance['requested_deployment_model']}`",
        f"- Required MCP-child backend: `{provenance['required_mcp_child_backend']}`",
    ]
    health = provenance.get("deployment_health")
    if isinstance(health, dict):
        payload = health.get("payload")
        if isinstance(payload, dict):
            lines.append(
                "- Deployment health before evaluation: "
                f"HTTP {health.get('http_status')}; status source `{payload.get('rag_status_source')}`; "
                f"child backend `{payload.get('rag_backend')}`; "
                f"configured backend `{payload.get('configured_rag_backend')}`; "
                f"model `{payload.get('rag_model')}`."
            )
        else:
            lines.append(f"- Deployment health before evaluation: HTTP {health.get('http_status')}; unavailable.")
    lines.append("")
    return lines


def render_repeated(
    records: list[dict[str, Any]], aggregate: dict[str, Any], provenance: dict[str, Any]
) -> str:
    """Render an honest median/range report for sequential repeated runs."""
    summaries = [record["summary"] for record in records]
    source = "local agent" if summaries[0]["mode"] == "local" else f"deployed HTTP: `{summaries[0]['base_url']}`"
    lines = [
        "# Evaluation Results",
        "",
        f"Mode: {source} · sequential full runs: {aggregate['run_count']} · "
        "generated by `python -m evaluation.run_eval`.",
        "",
        *_render_provenance(provenance),
        "## Repeated-run summary",
        "",
        "Each row is the median and min–max range across complete sequential runs. "
        "This shows observed variability; it is not a confidence interval or formal error bars.",
        "",
        "| Metric | Median | Range | Individual runs |",
        "| --- | --- | --- | --- |",
    ]
    for metric in aggregate["metrics"]:
        if metric["median"] is None:
            median, value_range = "n/a", "n/a"
        elif metric["unit"] == "rate":
            median = f"{metric['median']:.0%}"
            value_range = f"{metric['minimum']:.0%}–{metric['maximum']:.0%}"
        else:
            median = f"{metric['median']:.1f} ms"
            value_range = f"{metric['minimum']:.1f}–{metric['maximum']:.1f} ms"
        lines.append(
            f"| {metric['label']} | {median} | {value_range} | {'; '.join(metric['individual'])} |"
        )

    lines += [
        "",
        "## Individual run summaries",
        "",
        "| Run | UTC start | Planner | End-to-end | Answer rubric | Citation precision | Workflow | p95 latency |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        summary = record["summary"]
        lines.append(
            f"| {record['run_number']} | {record['started_at_utc']} | {summary['planner']} | "
            f"{summary['end_to_end_pass_rate']} | {summary['answer_rubric_accuracy']} | "
            f"{summary['citation_document_precision']} | {summary['workflow_completion']} | "
            f"{summary['latency_p95_ms']:.1f} ms |"
        )
    lines += [
        "",
        "All per-case synthetic answers, citations, MCP traces, and raw run summaries are retained "
        "in `evaluation/artifacts.json` for human review; no strongest run was selected for reporting.",
        "",
    ]
    return "\n".join(lines)


def _yes(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "y" if value else "n"


def render(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
    ablation: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> str:
    source = "local agent" if summary["mode"] == "local" else f"deployed HTTP: `{summary['base_url']}`"
    lines = [
        "# Evaluation Results",
        "",
        f"Mode: {source} · planner: `{summary['planner']}` · cases: {summary['cases']} · "
        "generated by `python -m evaluation.run_eval`.",
        "",
    ]

    if provenance is not None:
        lines += _render_provenance(provenance)

    lines += [
        "| Metric | Result |",
        "| --- | --- |",
        f"| Behaviour accuracy | {summary['behaviour_accuracy']} |",
        f"| Answer rubric accuracy | {summary['answer_rubric_accuracy']} |",
        f"| End-to-end pass rate | {summary['end_to_end_pass_rate']} |",
        f"| Citation document recall | {summary['citation_document_recall']} |",
        f"| Citation document precision | {summary['citation_document_precision']} |",
        f"| Citation complete required coverage | {summary['citation_complete_coverage']} |",
        f"| Multi-document complete coverage | {summary['multi_document_complete_coverage']} |",
        f"| Citation structure valid | {summary['citation_shape']} |",
        f"| Citation IDs resolve to local index | {summary['citations_resolve']} |",
        f"| Required-tool recall | {summary['tool_required_recall']} |",
        f"| Required-tool complete coverage | {summary['tool_required_coverage']} |",
        f"| Workflow completion | {summary['workflow_completion']} |",
        f"| Confirmation/action contract | {summary['action_confirmation_contract']} |",
        f"| Confirmed mock-action completion | {summary['confirmed_action_completion']} |",
        f"| Groundedness (automatic proxy) | {summary['groundedness_proxy']} |",
        f"| HTTP success | {summary['http_success']} |",
        f"| Latency p50 | {summary['latency_p50_ms']:.1f} ms |",
        f"| Latency p95 | {summary['latency_p95_ms']:.1f} ms |",
        "",
        "Answer rubric accuracy requires the case's explicit required claims and the configured "
        "gold-reference term coverage. Citation coverage now requires **every** declared policy "
        "document, not just one document from a multi-policy question. Tool coverage similarly "
        "requires every declared tool but does not penalise legitimate additional safety tools.",
        "",
        "Groundedness is an automatic proxy: it checks required-document coverage, citation shape, "
        "and (in local mode) that citation IDs resolve to the index. It cannot establish that every "
        "sentence is faithfully entailed by a cited chunk; retain human review for that question.",
        "",
        "Synthetic per-case answers, citations, tool traces, expected claims, and scores are saved in "
        "`evaluation/artifacts.json` for human review.",
        "",
    ]

    if summary["mode"] == "http":
        lines += [
            "In HTTP mode, citation IDs are not claimed to resolve unless the deployment exposes "
            "its index; document coverage and citation structure are still measured from the API response.",
            "",
        ]

    if ablation:
        lines += [
            "## Retrieval ablation",
            "",
            "Measured on the local retriever over cases that name expected documents. The first "
            "recall column is the legacy any-document hit rate; expected-document recall and complete "
            "coverage are stricter, especially for multi-document questions. Precision counts distinct "
            "retrieved document names, so repeated chunks from one policy cannot inflate the result. "
            "MRR is the mean reciprocal rank of the first required document.",
            "",
            "| Top-k | Any-doc recall | Expected-doc recall | Complete coverage | Doc precision | MRR |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in ablation:
            lines.append(
                f"| {row['k']} | {row['recall']} | {row['expected_document_recall']} | "
                f"{row['complete_coverage']} | {row['document_precision']} | {row['mrr']} |"
            )
        lines.append("")

    lines += [
        "## Per-case detail",
        "",
        "| ID | Type | Expected | Observed | Answer | Docs r/p | Tools | Action | HTTP | ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        document_scores = (
            f"{row['citation_document_recall']:.0%}/{row['citation_document_precision']:.0%}"
        )
        http_status = "local" if row["http_status"] is None else str(row["http_status"])
        lines.append(
            f"| {row['id']} | {row['type']} | {row['expected_behavior']} | {row['observed_behavior']} | "
            f"{_yes(row['answer_content_ok'])} | {document_scores} | "
            f"{_yes(row['tool_required_coverage_ok'])} | {_yes(row['action_ok'])} | "
            f"{http_status} | {row['latency_ms']:.0f} |"
        )
    return "\n".join(lines) + "\n"


ARTIFACT_FIELDS = (
    "id", "type", "question", "expected_answer", "expected_behavior", "answer",
    "required_claims_matched", "required_claims_total", "missing_claims", "prohibited_hits",
    "expected_documents", "cited_documents", "citations", "expected_tools", "tools_called",
    "trace", "mock_action", "planner", "overall_ok", "latency_ms", "http_status", "http_error",
)


def _artifact_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain bounded, synthetic evidence needed to audit one evaluation run."""
    return [{field: row.get(field) for field in ARTIFACT_FIELDS} for row in rows]


def write_artifacts(rows: list[dict[str, Any]], path: Path) -> None:
    """Persist single-run synthetic evidence in the original v1 artifact shape."""
    payload = {
        "notice": "Synthetic evaluation artifacts only. Do not add real employee or workplace data.",
        "cases": _artifact_cases(rows),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_repeated_artifacts(
    records: list[dict[str, Any]], path: Path, provenance: dict[str, Any], aggregate: dict[str, Any]
) -> None:
    """Persist every repeated-run response set in an explicit v2 artifact shape."""
    payload = {
        "schema_version": 2,
        "notice": "Synthetic evaluation artifacts only. Do not add real employee or workplace data.",
        "provenance": provenance,
        "aggregate": aggregate,
        "runs": [
            {
                "run_number": record["run_number"],
                "started_at_utc": record["started_at_utc"],
                "finished_at_utc": record["finished_at_utc"],
                "summary": record["summary"],
                "cases": _artifact_cases(record["rows"]),
            }
            for record in records
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def retrieval_ablation(cases: list[dict[str, Any]], ks: tuple[int, ...] = (1, 2, 4, 6, 8)) -> list[dict[str, Any]]:
    """Measure direct document recall, coverage, noise, and rank across k.

    A multi-policy answer is not fully retrieved merely because its first
    policy appears.  The old metric retained below as ``recall`` is useful for
    comparison with prior reports; the accompanying micro recall and complete
    coverage expose the more meaningful multi-document behaviour.
    """
    graded = [case for case in cases if case.get("expected_documents")]
    rows = []
    for k in ks:
        any_document_hits = 0
        complete_hits = 0
        expected_document_hits = 0
        expected_document_total = 0
        retrieved_document_hits = 0
        retrieved_document_total = 0
        reciprocal = 0.0
        for case in graded:
            expected = set(case["expected_documents"])
            retrieved = [chunk["document"] for chunk in search(case["question"], k)]
            # A tool returns chunks, but this is a document-level evaluation.
            # Preserve first appearance/order for MRR while deduplicating the
            # precision denominator and multi-document coverage calculation.
            unique_retrieved = list(dict.fromkeys(retrieved))
            matched = expected & set(unique_retrieved)
            ranks = [index for index, document in enumerate(retrieved, start=1) if document in expected]
            if ranks:
                any_document_hits += 1
                reciprocal += 1 / ranks[0]
            if expected.issubset(unique_retrieved):
                complete_hits += 1
            expected_document_hits += len(matched)
            expected_document_total += len(expected)
            retrieved_document_hits += len(matched)
            retrieved_document_total += len(unique_retrieved)
        rows.append({
            "k": k,
            "recall": f"{any_document_hits}/{len(graded)} ({any_document_hits / len(graded):.0%})",
            "expected_document_recall": (
                f"{expected_document_hits}/{expected_document_total} "
                f"({_safe_ratio(expected_document_hits, expected_document_total):.0%})"
            ),
            "complete_coverage": f"{complete_hits}/{len(graded)} ({complete_hits / len(graded):.0%})",
            "document_precision": (
                f"{retrieved_document_hits}/{retrieved_document_total} "
                f"({_safe_ratio(retrieved_document_hits, retrieved_document_total):.0%})"
            ),
            "mrr": round(reciprocal / len(graded), 3),
        })
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation", action="store_true", help="sweep local retrieval k")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="number of complete sequential evaluation runs (default: 1)",
    )
    parser.add_argument(
        "--base-url",
        help="POST evaluation cases to this deployed service's /chat endpoint instead of running locally",
    )
    parser.add_argument("--out", default=str(Path(__file__).parent / "results.md"))
    parser.add_argument(
        "--artifacts-out",
        default=str(Path(__file__).parent / "artifacts.json"),
        help="write synthetic per-case answer/citation/trace evidence to this JSON path",
    )
    parser.add_argument(
        "--deployment-revision",
        help="operator-supplied deployed Git revision recorded as non-secret provenance",
    )
    parser.add_argument(
        "--deployment-model",
        help="operator-supplied deployed model recorded as non-secret provenance",
    )
    parser.add_argument(
        "--require-rag-backend",
        choices=("lexical", "dense"),
        help="fail before evaluation unless /health verifies this MCP-child backend",
    )
    args = parser.parse_args()

    if args.base_url and not args.base_url.startswith(("http://", "https://")):
        parser.error("--base-url must start with http:// or https://")
    if args.base_url and args.ablation:
        parser.error("--ablation is local-only; omit it when using --base-url")
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.runs > 1 and args.ablation:
        parser.error("--ablation is a single deterministic local sweep; run it separately")
    if args.require_rag_backend and not args.base_url:
        parser.error("--require-rag-backend requires --base-url")

    cases = load_cases()
    if not args.base_url:
        build_index()

    deployment_health = await fetch_deployment_health(args.base_url) if args.base_url else None
    if args.require_rag_backend:
        _require_expected_backend(deployment_health or {}, args.require_rag_backend)
    provenance = build_provenance(
        base_url=args.base_url,
        run_count=args.runs,
        deployment_revision=args.deployment_revision,
        deployment_model=args.deployment_model,
        expected_backend=args.require_rag_backend,
        deployment_health=deployment_health,
    )

    if args.runs == 1:
        rows = await run_once(cases, base_url=args.base_url)
        summary = summarise(rows, base_url=args.base_url)
        ablation = retrieval_ablation(cases) if args.ablation else []
        Path(args.out).write_text(render(summary, rows, ablation, provenance), encoding="utf-8")
        write_artifacts(rows, Path(args.artifacts_out))
        print(json.dumps(summary, indent=2))
    else:
        records = await run_repeated(cases, args.runs, base_url=args.base_url)
        aggregate = aggregate_summaries([record["summary"] for record in records])
        Path(args.out).write_text(render_repeated(records, aggregate, provenance), encoding="utf-8")
        write_repeated_artifacts(records, Path(args.artifacts_out), provenance, aggregate)
        print(json.dumps({"provenance": provenance, "aggregate": aggregate}, indent=2))
    print(f"\nWrote {args.out} and {args.artifacts_out}")


if __name__ == "__main__":
    asyncio.run(main())
