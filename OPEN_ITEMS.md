# Open Items Before Submission

This file separates verified evidence from work that still requires an account,
a live host, or human review. It prevents a local deterministic test run—or an
old public evaluation—from being presented as evidence about a newer planner
revision.

## Verified: live LLM planner and dense-runtime public HTTP evaluation

Render is the submitted host. Its child-owned `/health` response, three
sequential 29-case public HTTP evaluation, and retained per-case artifacts are
recorded in `deployed.md`, `evaluation/results.md`, and
`evaluation/artifacts.json`. Before evaluation, the harness required
`rag_status_source: mcp_child`, matching child/parent backend `dense`, and a
loaded dense encoder. The three runs report a 76% median end-to-end pass rate
(66%–76% observed range), 76% median answer-rubric accuracy (69%–79%), 86%
median citation precision (83%–87%), and 100% HTTP success in every run.

The artifacts show real `planner: "llm"` responses alongside deterministic
safety-gate responses; they are not a fallback-only test. The report
deliberately labels that mixture rather than calling every case an LLM decision.
The earlier 66% single-run result predates `94639a7` and is retained only as a
historical lexical-MCP-child baseline, not as dense-RAG evidence.

The committed default is `gpt-5.6-luna`. A response proves that an LLM planner
ran, but it does not by itself prove the exact host model override or deployed
commit. Keep the host variable and deployment revision in the presenter notes;
never expose the API key.

## Required after each future planner/safety revision: re-run the public evaluation

After a change to the planner, RAG, MCP tool policy, or safety gate, deploy the
revision and run both the retrieval ablation and the public HTTP evaluation:

```bash
python -m evaluation.run_eval --ablation
python -m evaluation.run_eval --base-url https://clearhr-agentic-hr-assistant.onrender.com --runs 3 \
  --require-rag-backend dense --deployment-revision <deployed-sha> \
  --deployment-model gpt-5.6-luna
```

Commit the resulting `evaluation/results.md` and `evaluation/artifacts.json`.
The remote mode records HTTP status and client-observed latency but correctly
does not claim remote citation IDs resolve to the local index. Review failures;
do not weaken an answer rubric merely to inflate a score.

## Open: dense-host resource measurement

The submitted Render service intentionally uses `RAG_BACKEND=dense`; local/CI
and the unverified Railway path intentionally retain lexical retrieval. The
local retriever comparison in
[`evaluation/dense_rag_comparison.md`](evaluation/dense_rag_comparison.md)
improves default-k expected-document recall from 64% to 82% and complete
required-document coverage from 60% to 80%. Those are local retrieval results,
not a claim about every end-to-end answer.

Dense Python-3.11 `ensure_ready()` plus one query measured 292,932 KB maximum
RSS locally. The child-side dense backend and public evaluation are verified,
and the wake-from-idle cold start was measured on 2026-07-29 at 42.5 s
(`deployed.md`). Total Render host RSS remains unmeasured on the 512 MB free
service; record it before making a free-tier memory claim. Set `RAG_BACKEND=lexical` to roll back without code or data
migration if the host limit is approached.

## Blocking: share the repository with the grader

Share the exact `main` commit whose GitHub Actions checks are green with the
GitHub account `quantic-grader` before submission. This access change must be
performed by the repository owner and cannot be verified from the checked-in
source.

## Blocking: record the demo

Record the required 7–10 minute screen-share presentation. Use the live,
LLM-enabled deployed service. For each of two tasks, explain the tool names,
arguments, returned results, citations, and final answer or mock action. Also
show design, deployment, CI/CD, and measured evaluation results.

## Blocking: submit the project administratively

Use the course dashboard's **Submit Project** flow after the repository is
shared, deployed, and recorded. If this is a group submission, **only one
member submits** on behalf of the group, and that person uploads the completed,
signed final page of the Group Project Agreement when the dashboard requests
it. Do not upload credentials, host screenshots containing secrets, or
non-synthetic employee information.

## Should improve: conventional vector-store interpretation

The dense implementation is semantic and model-backed, but its vector store is
a deliberately small persisted JSON matrix searched by cosine rather than a
named FAISS/Chroma dependency. That is technically appropriate for 142 chunks,
but the rubric names conventional stores as examples. If a grader interprets
that wording strictly, evaluate a pinned `faiss-cpu` index only after the dense
host trial passes; do not add it pre-emptively and make the 512 MB deployment
less reliable merely for a label.

## Should improve: human-check groundedness

The harness proves that a citation resolves to an expected chunk; it does not
prove the answer faithfully represents that chunk. Manually review a documented
sample of the live-LLM answers, or add a clearly labelled LLM-as-judge review,
and record the method and score in `evaluation/results.md`.

## Verified locally

- 14 synthetic policy documents in Markdown, HTML, and TXT; roughly 15,969
  words (about 32 standard manuscript pages).
- Seven FastMCP tools: six agent capabilities plus one health-only retrieval
  diagnostic. The deployed agent uses a real stdio MCP handshake,
  discovery, and `call_tool` requests; CI runs an independent stdio check.
- 29 evaluation cases covering policy, multi-document, workflow, ambiguity,
  safety, confirmation, and out-of-scope requests.
- Local deterministic/safety evaluation: 29/29 answer rubrics, complete
  required citations/tools, workflow completions, confirmation checks, and the
  automatic groundedness proxy pass. Measured local p50/p95 latency is
  24.2/49.3 ms; this includes the stronger section-level evidence routing and
  excludes real LLM and public-host latency.
- `pytest`, `ruff`, the production Uvicorn smoke test, and the MCP protocol
  check pass.
