# Submission Requirements

The project brief's stated requirements, each mapped to where this repository
satisfies it and whether that has been verified. This exists so a requirement
cannot be lost between the brief, the code, and the recording.

Status keys: **done** — present and checked; **pending** — not yet done;
**owner-only** — requires an account or a recording only the submitter can make.

## 1. The demo

| Requirement | Where it is satisfied | Status |
| --- | --- | --- |
| For **each** agentic task, explain how the agent calls MCP tools: tool **names**, **arguments**, **outputs**, **retrieved citations**, and the **final answer or action** | [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) gives the screen sequence and the exact narration per tool. The `/chat` response carries `trace[].tool`, `trace[].arguments`, `trace[].result_preview`, `citations[]`, and `answer`, and the UI renders all five | **owner-only** |
| Quick walkthrough of **design** | Architecture diagrams in [design-and-evaluation.md](design-and-evaluation.md) | **owner-only** |
| Quick walkthrough of **deployment** | [deployed.md](deployed.md) and [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | **owner-only** |
| Quick walkthrough of **CI/CD** | [.github/workflows/ci.yml](.github/workflows/ci.yml); Render gates automatic deploys on `autoDeployTrigger: checksPass` rather than repeating the suite in the host build, and `/health` plus the startup log report the deployed commit | **owner-only** |
| Quick walkthrough of **evaluation results** | [evaluation/results.md](evaluation/results.md) | **owner-only** |
| Length 7–10 minutes | — | **pending** |

Two agentic tasks are prepared: the PTO request (`E1001`) and international
remote work (`E1003`). A workplace-conduct report additionally demonstrates the
safety gate and the confirmation-required mock ticket.

## 2. Repository sharing

| Requirement | Status |
| --- | --- |
| Share the repository with the GitHub account **`quantic-grader`** | **owner-only** |

## 3. Required repository contents

| Required | Present | Requirement met by |
| --- | --- | --- |
| All developed code | [app/](app/), [evaluation/](evaluation/), [scripts/](scripts/), [tests/](tests/) | **done** |
| `README.md` — introductory description, setup, local run, deployment instructions | [README.md](README.md) | **done** |
| `design-and-evaluation.md` | [design-and-evaluation.md](design-and-evaluation.md) — see the per-topic breakdown below | **done** |
| `ai-tooling.md` — which AI code tools were used and how, what worked well and what did not | [ai-tooling.md](ai-tooling.md) | **done** |
| `deployed.md` — deployed URL, health endpoint URL, free-tier cold-start notes | [deployed.md](deployed.md) | **done** — cold start measured 2026-07-29 at 42.5 s |
| `evaluation/` — questions, expected answers or rubrics, scripts, reported results | [evaluation/evaluation_set.json](evaluation/evaluation_set.json), [run_eval.py](evaluation/run_eval.py), [results.md](evaluation/results.md), [artifacts.json](evaluation/artifacts.json), and [dense_rag_comparison.md](evaluation/dense_rag_comparison.md) | **done** |
| `mock_data/` — synthetic employee, PTO, benefits and/or ticket data | [mock_data/](mock_data/) — `employees.json`, `pto_balances.json`, `benefits.json`. Tickets are drafted at request time and never persisted, so no ticket file exists; the brief requires this data only "if used" | **done** |
| `mcp/` — MCP server code and tool definitions | [mcp/README.md](mcp/README.md) documents the layer and the six tool schemas. The executable server is [app/mcp_server.py](app/mcp_server.py); it is **not** placed under `mcp/` because that directory would shadow the installed `mcp` SDK package on import. The reason is recorded in `mcp/README.md` | **done** |

### `design-and-evaluation.md` must explain each of these

| Required topic | Status |
| --- | --- |
| Architecture | **done** |
| RAG design | **done** |
| MCP server design | **done** |
| Agent orchestration | **done** |
| Tool schemas | **done** |
| Safety guardrails | **done** |
| Deployment choices | **done** |
| Evaluation **questions** | **done** — all 29 reproduced in the document |
| Evaluation **expected answers** | **done** — tabulated beside each question |
| Evaluation **results** | **done** |

## 4. Submission process

- Submit with the **Submit Project** button on the course dashboard.
- For a group, **only one member** submits on behalf of the group.
- A group submission also uploads the **final signed page of the Group Project
  Agreement**, completed and signed by every member.
- Questions: **msaie+projects@quantic.edu**.
- **There is no score penalty for a late submission**, though grading may be
  delayed. Correctness is therefore worth more than haste.

## Outstanding

Everything not marked **done** above, plus the technical gaps tracked in
[OPEN_ITEMS.md](OPEN_ITEMS.md) — chiefly the dense-RAG host memory/cold-start
measurement and the remaining submission actions.
