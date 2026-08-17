# Deployment Record

## URLs

- Application URL: https://clearhr-agentic-hr-assistant.onrender.com
- Health endpoint URL: https://clearhr-agentic-hr-assistant.onrender.com/health

**Host: Render.** This is the submitted deployment. Its current `/health`
response returns HTTP 200 with `status: ok`, `mcp_connected: true`,
`rag_status_source: mcp_child`, `rag_backend: dense`,
`configured_rag_backend: dense`, `rag_model: BAAI/bge-small-en-v1.5`,
`rag_dimensions: 384`, and `dense_encoder_loaded: true`. This is child-owned
runtime evidence, not an inference from the parent process or build log.
`/chat` reports `planner: "llm"`, so the deployed demo uses the live planner
rather than the deterministic fallback.

### Intentional RAG backend policy

- **Submitted Render service:** `dense`, after child-side health verification.
- **Local setup and CI:** `lexical`, for fast, deterministic, dependency-light
  tests.
- **Railway:** `lexical` by default until an independently verified dense
  Railway deployment is intentionally configured.

The Render build first creates the dense index; the `RAG_BACKEND=lexical`
suffix then scopes only the test subprocess. It does not switch the deployed
MCP child or Render runtime to lexical. `RAG_BACKEND=lexical` remains the
immediate one-variable rollback if a host cannot sustain dense resources.

### Verified dense-RAG evaluation

[evaluation/results.md](evaluation/results.md) records three complete,
sequential 29-case HTTP runs against this URL. Before the first billable case,
the evaluator required `/health` to prove a dense MCP child. The reported
median was a 76% end-to-end pass rate (66%–76% range), 76% answer-rubric
accuracy (69%–79%), 86% citation precision (83%–87%), and 100% HTTP success in
every run. The artifacts retain all three response sets and report the observed
range rather than selecting the strongest run.

The earlier 66% single-run result was a **historical lexical MCP-child
baseline** before the environment-forwarding fix. It is not used as evidence
for the current dense deployment. After any planner, RAG, MCP-tool-policy, or
safety change is deployed, repeat the public evaluation and replace the report
and artifacts before presenting updated results. The local retriever comparison
remains in [evaluation/dense_rag_comparison.md](evaluation/dense_rag_comparison.md).

A local Python-3.11 dense process measured 292,932 KB maximum RSS. The live
service's total parent-plus-MCP-child RSS remains unmeasured; record it before
presenting a free-tier memory claim. The wake-from-idle cold request is measured
below.
`RAG_BACKEND=lexical` remains the immediate rollback.

A Railway deployment, if any, is **not** part of the submission. Treat it as a
lexical default unless its own environment, child-side `/health`, resource
behavior, and live-planner status are independently verified. Do not use it as
evidence for this Render deployment.

### Cold start

Render's free instance sleeps after roughly 15 minutes of inactivity. Measured
on 2026-07-29 by leaving the service untouched for 16.5 minutes, then timing one
request from an external client:

| Request | Wall-clock | HTTP |
| --- | --- | --- |
| `GET /health` — first request after idle (**cold**) | **42.5 s** | 200 |
| `GET /health` — immediately after (warm) | 0.24 s | 200 |
| `POST /chat` — first answer after wake | 8.4 s | 200 |

The cold request is ~180x the warm one. It covers Render restoring the
container, the app building or loading the policy index, and the MCP child
loading the FastEmbed BGE model, which the lexical backend does not do. Nothing
is served until that completes, so a visitor arriving at a sleeping instance
waits the full 42.5 s with no feedback.

Two consequences for presenting this service:

- **Wake it before recording the demo or showing it to a grader.** One request
  to `/health` a minute beforehand is enough; the service then stays warm while
  in use.
- The 29-case evaluation in `evaluation/results.md` reports **warm** latency.
  Its median p50 of ~2.8 s (2.7–3.5 s across runs) is the steady-state figure
  and excludes this cold path, which is measured separately here on purpose.

## Deployment configuration

The repository includes `render.yaml` and `railway.toml`. Either creates one web service with a build command that creates the selected RAG index, then uses the `uvicorn` start command and `/health` health check. Render gates its automatic deploys on GitHub CI instead of repeating the suite in the build; Railway runs the tests in its build. Use only one host for the final submission, then paste its public URLs above. A healthy deployment returns HTTP 200 with `status: ok`, `mcp_connected: true`, `rag_status_source: mcp_child`, and matching child `rag_backend` / parent `configured_rag_backend`; HTTP 503 means the local MCP subprocess is unavailable or misconfigured and must be fixed before recording.

Follow [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for step-by-step Render and Railway instructions.

## Free-tier notes

The first request after inactivity may be slower because the host wakes the
service and starts the MCP subprocess, which loads the selected RAG backend.
The recorded three-run warm HTTP evaluation is verified dense-runtime evidence;
the cold path is measured separately under **Cold start** above. Do not enter
`OPENAI_API_KEY` or any other secret in this file.
