# Presentation Script — Three Speakers

A 7–10 minute recorded screen share, split across three presenters at roughly
three minutes each. Target **9:00**; the hard bounds are 7:00 and 10:00.

Every requirement from the brief is assigned to exactly one speaker below, so
nothing is dropped and nothing is said twice. Timings are the spoken budget, not
a stopwatch to obey mid-sentence.

| Speaker | Segment | Budget | Covers |
| --- | --- | --- | --- |
| 1 | Introduction + design + **Agentic task 1** | 3:00 | Design walkthrough; PTO task with full MCP narration |
| 2 | **Agentic task 2** + safety and confirmation | 3:00 | International remote-work task; action-safety proof |
| 3 | Deployment + CI/CD + evaluation + AI tooling | 3:00 | The three remaining required walkthroughs, plus close |

Speakers 1 and 2 each drive one of the two required agentic tasks, so the graded
core of the demo is not concentrated in one person. If you would rather have a
single demo driver, move task 1 to speaker 2 and give speaker 1 the design
walkthrough alone at a slower pace.

---

## Before recording — all three speakers

Run through [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) "Before recording" first, then:

1. **Wake the service.** Hit `/health` a minute before you start. A sleeping free
   instance costs **42.5 s** on the first request with no feedback on screen.
2. Confirm `/health` returns HTTP 200 with `mcp_connected: true`,
   `rag_status_source: mcp_child`, `rag_backend: dense`,
   `configured_rag_backend: dense`, `dense_encoder_loaded: true`.
3. Run both tasks once and confirm the response says `planner: "llm"`.
4. Expand **Retrieved citations** and **MCP tool calls** in the UI and leave them
   expanded.
5. Open in separate tabs: the deployed app, `/health`, the architecture diagram
   in `design-and-evaluation.md`, the GitHub Actions run, `evaluation/results.md`,
   `deployed.md`, `ai-tooling.md`.
   **Open the diagram on GitHub, not in the VS Code preview** — VS Code does not
   render mermaid without an extension, so the preview shows raw `flowchart`
   source. The `<details>` text-only diagram under it works anywhere.
6. Each speaker: camera on, voice on, **government ID shown when you first
   speak**. Say your own name before your segment.
7. Nobody screen-shares a `.env`, a host environment-variable value, a billing
   page, or anything non-synthetic.

**Read the trace that actually appears.** A live LLM may phrase the retrieval
query differently or add a `get_policy_section` call. Narrate the returned trace;
do not force it to match this page.

---

## Speaker 1 — Introduction, design, and agentic task 1

**Budget 3:00.** Screen: architecture diagram, then the live app.

### 0:00–0:25 — Open (say close to verbatim)

> "Good morning. I'm ⟨name⟩, with me are ⟨name⟩ and ⟨name⟩, and this is ClearHR —
> an agentic HR assistant for a fictional company, Northwind Systems. It answers
> employee HR questions by retrieving from a synthetic policy corpus and reading
> synthetic employee records, and every answer it gives is cited and traceable.
> I'll cover the design and run our first agentic task; ⟨name⟩ takes the second
> task and our safety controls; ⟨name⟩ closes with deployment, CI/CD, and our
> measured evaluation."

### 0:25–1:25 — Design walkthrough

Show the architecture diagram in `design-and-evaluation.md`. Point at each block
as you say it.

- **Corpus and RAG.** 14 synthetic policy documents, about 15,969 words, in three
  formats — 11 Markdown, 2 HTML, 1 plain text. Parsed heading-aware, so all 142
  chunks carry their document and section. Retrieval is dense: FastEmbed
  `BAAI/bge-small-en-v1.5`, 384 dimensions, cosine, top-k of 4. Citations carry
  document ID, section, and snippet.
- **The MCP boundary — say this explicitly, it is the rubric's hard line.** Seven
  FastMCP tools: two read the RAG index, four read or draft against synthetic
  records, and one is a health-only retrieval diagnostic that the model is never
  allowed to call. The agent does **not** call Python functions directly. At
  startup the service launches the FastMCP server as a local stdio subprocess,
  completes a real MCP handshake, discovers the tool schemas, and sends
  `call_tool` requests over JSON-RPC. Schemas are served live at `GET /tools`.
- **Orchestration.** Every request passes a deterministic safety gate first, then
  clarification and scope gates, then the LLM planner — a bounded tool-use loop on
  `gpt-5.6-luna` — with a deterministic rule-based planner as fallback if there is
  no key or the provider fails. The LLM chooses *which* tool; MCP is *how* the call
  travels. Those are separate concerns by design.
- One sentence on the trade-off, then move: "It's one free-tier service, so the
  MCP server runs as a local subprocess rather than a second hosted service — the
  protocol boundary is real either way."

### 1:25–2:55 — Agentic task 1: PTO request

Switch to the live app. Click the green **Demo 1** row in Quick-start scenarios —
it fills the question and sets `E1001`, so nothing is typed on camera — then submit:

> Can I take three days of PTO next week?

While it runs, say: "This is the live deployed service, and the response will
report `planner: llm`." Then narrate the **MCP tool calls** panel in order — name,
arguments, output — and finish on citations and the answer.

| MCP tool | Arguments to read aloud | Output to explain |
| --- | --- | --- |
| `search_policy_documents` | The retrieval query the planner sent, and the returned `limit` | Returns citable chunks from `pto_policy.md`, section **Request and Approval** — planned PTO needs at least five calendar days' notice and manager approval |
| `lookup_employee_profile` | `{"employee_id": "E1001"}` | Synthetic profile: manager **Morgan Lee**, New York office |
| `check_pto_balance` | `{"employee_id": "E1001"}` | Synthetic balance: **40 available hours**, which is five eight-hour days |

Then, pointing at the two panels:

- **Citations:** "`pto_policy.md`, section *Request and Approval*, with the snippet
  the answer relied on — document, section, and text, not just a filename."
- **Final answer:** "It combines the policy rule with this employee's record —
  three days fits the 40-hour balance, but next week is inside the five-day notice
  window, so it tells the employee what approval is required. And it submits
  nothing. No real PTO request exists after this call."

Hand off: "⟨name⟩ will take the second task, which is a harder multi-policy one."

---

## Speaker 2 — Agentic task 2 and action safety

**Budget 3:00.** Screen: the live app throughout.

### 0:00–0:10 — Open

> "I'm ⟨name⟩. Our second task needs more than one policy document and the
> employee's location record at the same time."

### 0:10–2:00 — Agentic task 2: international remote work

Click the green **Demo 2** row in Quick-start scenarios — it fills the question
and sets `E1003` — then submit:

> I am based in California and want to work from Portugal for six weeks. What
> approvals and security requirements apply?

Narrate the returned trace. Expect policy retrieval — possibly a
`get_policy_section` call after the search — plus the profile lookup. Show it if
it appears; do not pretend it did if it did not.

| MCP tool / evidence | What to say |
| --- | --- |
| `search_policy_documents` (and `get_policy_section` if present) | Read the query and arguments aloud. The returned `remote_work_policy.md` **International Work** evidence requires written approval from People Operations, Security, Payroll, and the employee's vice president before working internationally — and for six weeks, an advance request plus a tax and permanent-establishment review |
| The data-security citation returned | Explain only what the response actually cites — company-managed equipment, encryption, MFA, VPN, no copies to personal cloud or email |
| `lookup_employee_profile` | Read `{"employee_id": "E1003"}` aloud. The synthetic California / San Francisco record is what makes this a structured-data task as well as a retrieval task |

Close the task on the two panels: "Multiple documents cited, each with its
section and snippet, and the final answer ties the approval chain to this
employee's actual location. It is reporting fictional policy — it is not making a
legal, tax, or immigration determination, and approval is not guaranteed."

### 2:00–2:50 — Safety and the confirmation gate

Keep the confirmation checkbox **off**. Submit the synthetic prompt:

> My manager has been harassing me. Please create a mock HR ticket now.

- "Conduct reports hit a deterministic safety gate **before** the model is
  consulted at all, so an escalation can never depend on a model judgement call.
  It routes to People Operations and the reporting channel, and — look at the
  trace — no `mock_action` was created, even though the user asked for one."
- Now tick the checkbox and resubmit. "The UI flag becomes `confirm_mock_action`.
  Only then does the planner add `confirmed: true`, and `create_mock_hr_ticket`
  independently rejects an unconfirmed call at the MCP layer — two places, not
  one. The result comes back `mock_only: true` with `confirmation_obtained`. It is
  a draft. Nothing is filed, nothing is investigated, nothing is irreversible."

Hand off: "⟨name⟩ will show where this runs and how well it actually scores."

---

## Speaker 3 — Deployment, CI/CD, evaluation, and AI tooling

**Budget 3:00.** Screen: Render dashboard → `/health` → GitHub Actions →
`evaluation/results.md` → `ai-tooling.md`.

### 0:00–0:50 — Deployment

Show the Render service and `deployed.md`.

- "One free-tier Render web service, `clearhr-agentic-hr-assistant.onrender.com`,
  with the FastAPI app and the MCP subprocess in the same service."
- Open `/health` live. "This is child-owned evidence, not the parent process
  guessing: `rag_status_source: mcp_child`, `rag_backend: dense` matching
  `configured_rag_backend: dense`, `dense_encoder_loaded: true`, and the deployed
  commit. `mcp_connected: true` means the stdio handshake succeeded."
- Show the host environment-variable screen **with values hidden**. "The API key
  only ever exists in the host's environment settings."
- Free-tier honesty, and say it plainly: "The instance sleeps after about 15
  minutes. We measured the wake-from-idle request at **42.5 seconds**, against
  0.24 seconds warm. That is why we warmed the service before recording, and it is
  written down in `deployed.md` rather than hidden. `RAG_BACKEND=lexical` is a
  one-variable rollback if the 512 MB host ever can't sustain the dense encoder."

### 0:50–1:30 — CI/CD

Show `.github/workflows/ci.yml` and a green Actions run.

- "Every push runs ruff, an import check, a lexical index build, the pytest suite,
  a production Uvicorn app-start and health smoke test, and — separately —
  `mcp_check.py`, which does an independent MCP stdio discovery and a live tool
  call. That last one is what proves the MCP layer works outside our own app."
- "Render is gated on `autoDeployTrigger: checksPass`, so a red GitHub check
  means no deploy. The host build creates the dense index instead of re-running
  the suite, and `/health` reports the deployed commit so we can always tell which
  revision is live."

### 1:30–2:35 — Evaluation results

Show `evaluation/evaluation_set.json`, then `evaluation/results.md`.

- "29 evaluation cases — policy, multi-document, workflow, ambiguity, safety,
  confirmation, and out-of-scope. Every question and its expected answer or rubric
  is in `design-and-evaluation.md`."
- "These numbers are three complete sequential runs against the **public deployed
  URL**, with a dense MCP child verified before the first case. We report the
  median and the observed range — we did not pick our best run."

| What we measured | Median | Range |
| --- | --- | --- |
| Behaviour accuracy | 97% | 97–100% |
| End-to-end pass rate | 76% | 66–76% |
| Answer rubric accuracy | 76% | 69–79% |
| Citation document precision / recall | 86% / 89% | 83–87% / 89–96% |
| Citation structure valid | 100% | every run |
| Required-tool recall and coverage | 97% | 90–97% |
| Workflow completion | 61% | 44–61% |
| Confirmation / action contract | 100% | every run |
| Groundedness (automatic proxy) | 90% | 90–97% |
| HTTP success | 100% | every run |
| Latency p50 / p95 (warm) | 2.76 s / 7.17 s | p95 6.1–7.4 s |

Say these three things out loud — they are what a grader is listening for:

- **The ablation.** "We ablated the retrieval representation. On the 20
  retrieval-labelled cases, dense retrieval reached 82% expected-document recall
  and 80% complete required-document coverage against lexical's 64% and 60%, with
  MRR .875 versus .775. That is a retriever measurement, not a claim that every
  end-to-end answer improved."
- **The limitation we are not hiding.** "Groundedness there is an automatic proxy
  — it proves a citation resolves to an expected chunk, not that the sentence
  faithfully represents it. Human or LLM-judge review is recorded as open work."
- **The weakest number, owned.** "Workflow completion at 61% median is our
  weakest metric and it's the honest one to point at. Action safety and citation
  structure were 100% in every run — the system fails by being incomplete, not by
  being unsafe or by inventing a source."

### 2:35–3:00 — AI tooling and close

Show `ai-tooling.md` briefly.

> "On tooling: Codex wrote the first draft, Claude did a second pass and the
> verification work, and `ai-tooling.md` records what each was good at and where
> it was wrong — the retrieval representation that collapsed on the full corpus,
> and an environment variable that silently wasn't reaching the MCP subprocess,
> are both written up there. To summarise: a real MCP boundary with seven
> discovered tools, cited dense retrieval over 14 synthetic policy documents, two
> multi-step workflows you saw end to end, mock-only confirmed actions, CI-gated
> deployment on a free tier, and 29 evaluation cases measured three times against
> the live URL. Thank you."

---

## Final check before you submit the recording

- [ ] Runtime is between 7:00 and 10:00.
- [ ] All three speakers appeared on camera, spoke, and showed government ID.
- [ ] Both agentic tasks visibly showed tool **name**, exact **arguments**,
      **returned output**, **citations**, and the **final answer or action**.
- [ ] Both primary tasks reported `planner: "llm"`.
- [ ] Design, deployment, CI/CD, and evaluation were each walked through.
- [ ] No secret, host billing page, or non-synthetic personal information appeared.
- [ ] The repository is shared with `quantic-grader` at the green-CI commit.
- [ ] One member submits, with the signed final page of the Group Project
      Agreement.
