# SPEAKER 2 — CUE CARDS

### Mehdi · Budget: 3:00 · Keep beside your screen

Read left to right: the **ON SCREEN** column is what your viewer should be
looking at while you say the <span style="color:#15803d">**green SAY**</span>
column. Never say a line whose screen is not up yet.

> 🟢 Open this in the **VS Code markdown preview** (`Ctrl+Shift+V`) or a browser —
> that's where the green shows. GitHub strips the colour and it reads as plain
> text, which is fine but harder to scan while recording.

---

## 🎬 0:00–0:10 — OPENING

| ON SCREEN | SAY |
| --- | --- |
| 📌 Camera on. Gov ID held up. Live app already open. | <span style="color:#15803d">"I'm Mehdi. Our second task needs more than one policy document and the employee's location record at the same time."</span> |

---

## 🌍 0:10–2:00 — AGENTIC TASK 2: INTERNATIONAL REMOTE WORK

| ON SCREEN — do this | SAY |
| --- | --- |
| **Quick-start scenarios** → click the green **Demo 2** row. It fills the question and sets **E1003** for you — no typing. | <span style="color:#15803d">"Same deployed service, different employee."</span> |
| Confirm it reads *I am based in California and want to work from Portugal for six weeks…*, then click **Ask ClearHR** | <span style="color:#15803d">"This one needs more than one policy document at once."</span> |
| **MCP tool calls** panel → `search_policy_documents` | <span style="color:#15803d">Read the query and arguments aloud. "The returned `remote_work_policy.md` **International Work** evidence requires written approval from People Operations, Security, Payroll, and the employee's vice president before working internationally — and for six weeks, an advance request plus a tax and permanent-establishment review."</span> |
| `get_policy_section` — **only if it actually fired** | <span style="color:#15803d">Read the arguments aloud. "A targeted section pull after the initial search."</span> Show it if it's there; don't pretend it is if it isn't. |
| The data-security citation in the citations panel | <span style="color:#15803d">Explain only what the response actually cites — company-managed equipment, encryption, MFA, VPN, no copies to personal cloud or email.</span> |
| `lookup_employee_profile` | <span style="color:#15803d">Read `{"employee_id": "E1003"}` aloud. "The synthetic California / San Francisco record is what makes this a structured-data task as well as a retrieval task."</span> |
| Both panels side by side, then the final answer | <span style="color:#15803d">"Multiple documents cited, each with its section and snippet, and the final answer ties the approval chain to this employee's actual location. It is reporting fictional policy — it is not making a legal, tax, or immigration determination, and approval is not guaranteed."</span> |

⚠️ **Read the trace that actually appears.** If the LLM adds or skips a call,
narrate the real trace.

---

## 🛡️ 2:00–2:50 — SAFETY & CONFIRMATION GATE

### Step 1 — Safety gate, checkbox **OFF**

| ON SCREEN — do this | SAY |
| --- | --- |
| Confirmation checkbox visibly **unticked** | <span style="color:#15803d">"Watch the confirmation checkbox — it's off."</span> |
| **Quick-start scenarios** → click the green **Demo 3** row (fills the prompt, sets E1001), then click **Ask ClearHR** | <span style="color:#15803d">"Conduct reports hit a deterministic safety gate **before** the model is consulted at all, so an escalation can never depend on a model judgement call. It routes to People Operations and the reporting channel."</span> |
| Point at the trace — no `mock_action` | <span style="color:#15803d">"Look at the trace — no `mock_action` was created, even though the user asked for one."</span> |

### Step 2 — Confirmation gate, checkbox **ON**

| ON SCREEN — do this | SAY |
| --- | --- |
| Tick the confirmation checkbox — the prompt is still in the box — and click **Ask ClearHR** again | <span style="color:#15803d">"The UI flag becomes `confirm_mock_action`. Only then does the planner add `confirmed: true`, and `create_mock_hr_ticket` independently rejects an unconfirmed call at the MCP layer — two places, not one."</span> |
| Point at `mock_only: true` and `confirmation_obtained` in the result | <span style="color:#15803d">"It is a draft. Nothing is filed, nothing is investigated, nothing is irreversible."</span> |

---

## 🤝 HANDOFF

| ON SCREEN | SAY |
| --- | --- |
| Leave the mock result on screen; hand over | <span style="color:#15803d">"\_\_\_\_\_ will show where this runs and how well it actually scores."</span> |

---

## ⛔ DO NOT

- Screen-share `.env`, real keys, or billing pages
- Force the trace to match this card — read what's there
- Go over 3:00
