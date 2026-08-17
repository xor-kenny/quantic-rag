# SPEAKER 3 — CUE CARDS

### \_\_\_\_\_ · Budget: 3:00 · Keep beside your screen

Read left to right: the **ON SCREEN** column is what your viewer should be
looking at while you say the <span style="color:#15803d">**green SAY**</span>
column. Never say a line whose screen is not up yet.

> 🟢 Open this in the **VS Code markdown preview** (`Ctrl+Shift+V`) or a browser —
> that's where the green shows. GitHub strips the colour and it reads as plain
> text, which is fine but harder to scan while recording.

---

## 🚀 0:00–0:50 — DEPLOYMENT

| ON SCREEN — do this | SAY |
| --- | --- |
| 📌 Camera on. Gov ID held up. Render dashboard + `deployed.md` | <span style="color:#15803d">"I'm \_\_\_\_\_. One free-tier Render web service, `clearhr-agentic-hr-assistant.onrender.com`, with the FastAPI app and the MCP subprocess in the same service."</span> |
| Open `/health` live; point at each field as you name it | <span style="color:#15803d">"This is child-owned evidence, not the parent process guessing: `rag_status_source: mcp_child`, `rag_backend: dense` matching `configured_rag_backend: dense`, `dense_encoder_loaded: true`, and the deployed commit."</span> |
| Point at `mcp_connected` | <span style="color:#15803d">"`mcp_connected: true` means the stdio handshake succeeded."</span> |
| Host environment-variable screen — **values hidden** | <span style="color:#15803d">"The API key only ever exists in the host's environment settings."</span> |
| Back to `deployed.md`, on the free-tier section | <span style="color:#15803d">"The instance sleeps after about 15 minutes. We measured the wake-from-idle request at **42.5 seconds**, against 0.24 seconds warm. That's why we warmed the service before recording, and it's written down here rather than hidden."</span> |
| Same page, the rollback line | <span style="color:#15803d">"`RAG_BACKEND=lexical` is a one-variable rollback if the 512 MB host ever can't sustain the dense encoder."</span> |

---

## ⚙️ 0:50–1:30 — CI/CD

| ON SCREEN — do this | SAY |
| --- | --- |
| `.github/workflows/ci.yml`, scrolling the job steps | <span style="color:#15803d">"Every push runs ruff, an import check, a lexical index build, the pytest suite, a production Uvicorn app-start and health smoke test, and — separately — `mcp_check.py`."</span> |
| Point at the `mcp_check.py` step | <span style="color:#15803d">"That one does an independent MCP stdio discovery and a live tool call. It's what proves the MCP layer works outside our own app."</span> |
| Switch to a **green** GitHub Actions run | <span style="color:#15803d">"Render is gated on `autoDeployTrigger: checksPass`, so a red GitHub check means no deploy."</span> |
| Point back at `/health` tab, the commit field | <span style="color:#15803d">"The host build creates the dense index instead of re-running the suite, and `/health` reports the deployed commit so we can always tell which revision is live."</span> |

---

## 📊 1:30–2:35 — EVALUATION RESULTS

| ON SCREEN — do this | SAY |
| --- | --- |
| `evaluation/evaluation_set.json`, scroll the categories | <span style="color:#15803d">"29 evaluation cases — policy, multi-document, workflow, ambiguity, safety, confirmation, and out-of-scope. Every question and its expected answer or rubric is in `design-and-evaluation.md`."</span> |
| Switch to `evaluation/results.md`, top of the table | <span style="color:#15803d">"These numbers are three complete sequential runs against the **public deployed URL**, with a dense MCP child verified before the first case. We report the median and the observed range — we did not pick our best run."</span> |

### The table on screen — don't read every row, land on the ones below

| Metric | Median | Range |
| --- | --- | --- |
| Behaviour accuracy | 97% | 97–100% |
| End-to-end pass rate | 76% | 66–76% |
| Answer rubric accuracy | 76% | 69–79% |
| Citation doc precision / recall | 86% / 89% | 83–87% / 89–96% |
| Citation structure valid | 100% | every run |
| Required-tool recall & coverage | 97% | 90–97% |
| Workflow completion | 61% | 44–61% |
| Confirmation / action contract | 100% | every run |
| Groundedness (auto proxy) | 90% | 90–97% |
| HTTP success | 100% | every run |
| Latency p50 / p95 (warm) | 2.76 s / 7.17 s | p95 6.1–7.4 s |

### ⚠️ Three things graders listen for — say all three

| ON SCREEN — point here | SAY |
| --- | --- |
| The ablation section of `results.md` | **1 — The ablation.** <span style="color:#15803d">"We ablated the retrieval representation. On the 20 retrieval-labelled cases, dense retrieval reached 82% expected-document recall and 80% complete required-document coverage against lexical's 64% and 60%, with MRR .875 versus .775. That is a retriever measurement, not a claim that every end-to-end answer improved."</span> |
| The groundedness row | **2 — The limitation we're not hiding.** <span style="color:#15803d">"Groundedness there is an automatic proxy — it proves a citation resolves to an expected chunk, not that the sentence faithfully represents it. Human or LLM-judge review is recorded as open work."</span> |
| The workflow-completion row, then the two 100% rows | **3 — The weakest number, owned.** <span style="color:#15803d">"Workflow completion at 61% median is our weakest metric and it's the honest one to point at. Action safety and citation structure were 100% in every run — the system fails by being incomplete, not by being unsafe or by inventing a source."</span> |

---

## 🏁 2:35–3:00 — AI TOOLING & CLOSE

| ON SCREEN — do this | SAY |
| --- | --- |
| `ai-tooling.md`, scroll briefly | <span style="color:#15803d">"On tooling: Codex wrote the first draft, Claude did a second pass and the verification work, and `ai-tooling.md` records what each was good at and where it was wrong — the retrieval representation that collapsed on the full corpus, and an environment variable that silently wasn't reaching the MCP subprocess, are both written up there."</span> |
| Back to the live app (or the architecture diagram) for the close | <span style="color:#15803d">"To summarise: a real MCP boundary with seven discovered tools, cited dense retrieval over 14 synthetic policy documents, two multi-step workflows you saw end to end, mock-only confirmed actions, CI-gated deployment on a free tier, and 29 evaluation cases measured three times against the live URL. Thank you."</span> |

---

## ⛔ DO NOT

- Screen-share `.env` values or billing pages
- Skip any of the three "say out loud" eval points
- Go over 3:00
