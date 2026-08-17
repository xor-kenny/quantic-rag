# ClearHR: Agentic HR Policy Assistant

ClearHR is a synthetic, free-tier-friendly HR assistant built for the AI Engineering Techniques and Architectures project. An employee asks an HR question; the agent decides which tools it needs, retrieves the relevant company policy, looks up the employee's synthetic PTO or benefits record when the question calls for it, and answers with citations plus a trace of every tool call it made.

Every policy and every employee record in this repository is fictional.

**Deployed URL:** [ClearHR on Render](https://clearhr-agentic-hr-assistant.onrender.com) · [health](https://clearhr-agentic-hr-assistant.onrender.com/health). This service runs the live LLM planner. Deployment status, cold-start notes, and the planner verification are recorded in [deployed.md](deployed.md).

## Architecture

```text
Browser /chat UI → FastAPI → agent orchestrator → MCP client
                                   │                  │
                              OpenAI API        ══ MCP boundary ══
                            (tool selection)           │
                                              ClearHR MCP server
                                                ↙            ↘
                                    policy RAG index    synthetic records
```

The orchestrator never reads the policy index or the employee records directly. Its only route to either is a tool call dispatched through the MCP layer. Full diagrams, tool schemas, and design rationale are in [design-and-evaluation.md](design-and-evaluation.md).

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional; add OPENAI_API_KEY to enable the LLM planner
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` and try `E1001` with *"Can I take three days of PTO next week?"*.

| Endpoint | Purpose |
| --- | --- |
| `POST /chat` | Answer, citations, and tool-call trace |
| `GET /health` | App status, MCP connectivity, and the child process's effective RAG backend |
| `GET /tools` | Live MCP tool schemas as discovered by the agent, each marked with whether the planner may call it |
| `GET /usage` | Per-instance counts of LLM provider calls and MCP tool calls |
| `POST /usage/mark` | Start a fresh counter window so one demo segment is attributable to it; process totals are retained |

**Without an API key the app still runs.** It falls back to a deterministic rule-based planner over the same MCP tools, so every endpoint works and the test suite passes with no credentials. With `OPENAI_API_KEY` set, an LLM chooses the tools instead. The default model is the cost-sensitive `gpt-5.6-luna`; it requires a funded account with access to that model. In that mode, the user prompt and the tool schemas/results needed for the turn are sent to OpenAI; use this only with the repository's synthetic coursework data. Render has already produced a live-LLM HTTP evaluation; re-run it whenever a new planner or guardrail revision is deployed.

ClearHR keeps its explicit Chat Completions + MCP function-tool loop. For GPT-5.6, it explicitly sends `reasoning_effort="none"`, which is required for that endpoint's function-tool compatibility. Moving the planner to the Responses API to combine model reasoning with tools is a separate, unimplemented migration.

If a non-empty key produces `planner: "deterministic-fallback"`, the provider call failed after the fallback path was selected. Inspect the secure host log for the exact exception. For temporary local troubleshooting, set `EXPOSE_PLANNER_ERRORS=true`; the response then includes only the exception class, HTTP status, and provider error code—not raw provider text. Turn it off before a public demo or deployment.

## Corpus

14 synthetic policy documents, 15,969 words, in three formats — 11 Markdown, 2 HTML, 1 plain text — covering PTO, holidays, remote work, expenses, travel, equipment, benefits, leave, onboarding, data security, workplace conduct, compensation, performance, and health and safety. All three formats are parsed heading-aware so citations carry a real section name.

Retrieval has two versioned, interchangeable local backends with the same chunk IDs and MCP output schema:

- `RAG_BACKEND=lexical` — the deterministic sparse IDF/hash index, the safe local/CI default and rollback path.
- `RAG_BACKEND=dense` — FastEmbed's local `BAAI/bge-small-en-v1.5` model creates 384-dimensional BGE vectors and stores them in `data/index.dense.json`; the small corpus is searched by in-process cosine rather than an unnecessary external database service.

### Intentional backend policy

The backend is selected by execution context, rather than forcing dense RAG everywhere:

| Context | Selected backend | Reason |
| --- | --- | --- |
| Submitted Render service | `dense` | The Blueprint pins it after a child-side health check and a three-run deployed evaluation verified the dense MCP child. |
| Local setup, `.env.example`, and GitHub Actions | `lexical` | Deterministic, fast, and independent of an embedding-model download or dense-model memory use. |
| Railway guide/configuration | `lexical` until separately verified | Railway is not the submitted host; dense should be enabled there only for a measured deployment trial. |
| Host test phase | `lexical` | The selected production index is built first; the test command is then deliberately hermetic. |

The host build creates the selected index. At runtime, only the persistent MCP subprocess loads the optional dense model, preventing a duplicate model copy in the FastAPI parent. Render's build no longer runs tests, so it builds the index under the Blueprint's `RAG_BACKEND=dense` and nothing else. Railway's build keeps its test run, and there the `RAG_BACKEND=lexical` prefix scopes only `pytest`; it does **not** switch the preceding `scripts/build_rag_index.py` step or the runtime back to lexical. The trace preserves all returned evidence; the final citation list is deliberately limited to chunks that directly support the answer. Commit `94639a7` forwards a narrow, non-secret retrieval-settings allow-list to that child, and the current revision adds a child-owned retrieval-status diagnostic to `/health`. The current Render health response proves `rag_status_source: "mcp_child"`, `rag_backend: "dense"`, and `dense_encoder_loaded: true`; [evaluation/results.md](evaluation/results.md) records the corresponding three-run dense deployment evaluation. Lexical remains the local/CI default and one-variable rollback path.

## MCP tools

Seven MCP tools are exposed: six agent capabilities — `search_policy_documents`, `get_policy_section`, `lookup_employee_profile`, `check_pto_balance`, `lookup_benefits_status`, and `create_mock_hr_ticket` — plus `get_retrieval_status`, an operational-only child diagnostic. Two agent tools read the RAG index, four read or draft against synthetic records. Schemas are generated from type hints and served live at `/tools`. The LLM receives dynamically discovered schemas only for the explicitly authorised tool capabilities; `get_retrieval_status` and any future tool remain unavailable to the model until deliberately classified. See [mcp/README.md](mcp/README.md).

The deployed web service starts the MCP server as a local subprocess and calls it over stdio. It remains one free-tier service, but tool execution still crosses a real MCP protocol boundary:

```bash
python -m app.mcp_server          # run as a real MCP process
python scripts/mcp_check.py       # CI proof: handshake, list_tools, two real tool calls
```

## Demo tasks

1. **PTO request** — `E1001`, *"Can I take three days of PTO next week?"* The agent calls `search_policy_documents`, `lookup_employee_profile`, and `check_pto_balance`, then reports the 40-hour balance, names the approving manager, and cites the five-calendar-day notice requirement.
2. **International remote work** — `E1003`, *"I am based in California and want to work from Portugal for six weeks. What approvals and security requirements apply?"* The agent combines policy retrieval with the synthetic employee profile, then cites the remote-work and data-security evidence.

A workplace-conduct report additionally demonstrates the safety gate and the confirmation-required mock ticket.

Use [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) when recording: it gives the exact screen sequence and the MCP evidence to narrate for each task.

## Testing and CI

```bash
python -m pytest -q                 # RAG, MCP, planner-loop, API, and evaluation tests
ruff check app tests evaluation scripts
python scripts/smoke_test.py        # boots the production command, checks /health
python scripts/mcp_check.py         # stdio MCP discovery, child status, and two live tool calls
RAG_BACKEND=dense python scripts/build_rag_index.py  # build/cache dense local vectors
```

GitHub Actions runs the lint, import, lexical-index build, full test suite,
app-start smoke test, and MCP protocol check on every push and pull request.
The dense build is selected explicitly for a dense host or local trial rather
than imposed on every CI run.

## Deployment: Render or Railway

Configured for either platform as a **single web service**, with no database and no persistent disk. Click-by-click instructions are in [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

- **Render** — *New → Blueprint*, select this repository. [render.yaml](render.yaml) declares the runtime, build and start commands, free plan, and `/health` check.
- **Railway** — create a project from the repository. [railway.toml](railway.toml) configures Railpack, the same build command, and the health check. Generate a public domain after the first successful deploy.

Deployment builds the configured RAG index. On Render, [render.yaml](render.yaml) uses `autoDeployTrigger: checksPass`, so automatic deploys wait for GitHub CI and do not repeat the test suite or install dev-only packages in the host build; Railway retains its in-host test gate. Set `OPENAI_API_KEY` in the host's environment-variable settings only; never commit it. Leave `OPENAI_MODEL` unset so the cost-sensitive `gpt-5.6-luna` default applies, or set it explicitly to the same value. The running app launches and calls its MCP server over stdio, even in the single-service deployment. `/health` returns HTTP 503 if that MCP connection is unavailable **or** if the web parent and MCP child disagree on the backend. Its `rag_backend` comes from the child-owned `get_retrieval_status` MCP call, while `configured_rag_backend` shows the parent setting and `rag_status_source: "mcp_child"` proves this newer health contract is deployed. `commit` reports the host-injected revision the running process was built from, so a rollback or a surviving old instance is visible from the URL rather than only from the host dashboard. The submitted Render service intentionally uses dense RAG; local/CI and the unverified Railway path intentionally retain lexical as the dependency-light default and rollback.

`/chat` also has a small process-local cost guard by default (30 requests per client and 60 total per 60 seconds). It is suitable for a one-instance coursework demo, not a replacement for authentication, an edge rate limiter, or a production privacy review.

## Sharing with classmates safely

Use [COLLABORATION_GUIDE.md](COLLABORATION_GUIDE.md) before inviting collaborators or sharing a copy of the project. It covers safe local setup, which data is synthetic, GitHub access limitations, secret handling, and the response if a key is exposed.

## Evaluation

```bash
python -m evaluation.run_eval              # writes results.md and synthetic artifacts.json
python -m evaluation.run_eval --ablation   # local retrieval sweep: TOP_K = 1 / 2 / 4 / 6 / 8
python -m evaluation.run_eval --base-url https://your-service.example
python -m evaluation.run_eval --base-url https://your-service.example --runs 3 \
  --require-rag-backend dense --deployment-revision <deployed-sha> \
  --deployment-model gpt-5.6-luna
```

29 cases span single-document policy questions, multi-document questions, tool-requiring workflows, ambiguous requests, safety escalation, both unconfirmed and confirmed mock actions, and out-of-scope refusals. The harness checks explicit answer rubrics, complete citation/tool coverage, action confirmation, a groundedness proxy, and p50/p95 latency. `--base-url` measures the deployed HTTP service and does not falsely claim its citation IDs resolve to the local index. `--runs 3` performs three complete sequential runs, preserves all cases in an artifact-v2 file, and reports median/min–max observed variation rather than a cherry-picked result or formal confidence interval. `--require-rag-backend dense` refuses to spend evaluation requests unless the child-side health status confirms dense. See [evaluation/results.md](evaluation/results.md).

## Submission checklist

- [x] Repository contains all developed code and the required artefacts: this README,
      `design-and-evaluation.md`, `ai-tooling.md`, `deployed.md`, `evaluation/`,
      `mock_data/`, and `mcp/` documentation/tool definitions
- [x] Deploy and record the live URL and `/health` URL in [deployed.md](deployed.md)
- [x] Verify dense retrieval in the Render MCP child and commit the three-run
      live-LLM HTTP evaluation (median and observed range retained)
- [ ] Share the repository with the `quantic-grader` GitHub account
- [ ] Record the 7–10 minute demo: two agentic tasks end to end, narrating tool names, arguments, outputs, citations, and the final answer, then a walkthrough of design, deployment, CI/CD, and evaluation
- [ ] Submit through the course dashboard. For a group, one member submits on behalf of
      the group and uploads the signed final page of the Group Project Agreement if asked

The brief's requirements are tracked item by item, with the evidence for each, in [SUBMISSION_REQUIREMENTS.md](SUBMISSION_REQUIREMENTS.md). Known gaps, verified evidence, and deliberate trade-offs are tracked in [OPEN_ITEMS.md](OPEN_ITEMS.md), including the remaining dense-host resource measurement, cold-start measurement, and submission actions.

## AI assistance

Codex produced the initial scaffold; Claude Code did the second pass. Both are documented in [ai-tooling.md](ai-tooling.md).
