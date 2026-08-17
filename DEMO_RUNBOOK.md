# ClearHR Demo Runbook

Use this runbook for the recorded presentation. It is deliberately written for
the **live, LLM-enabled deployed service**; the deterministic fallback is useful
for local development but is not evidence that the real planner was tested.

## Before recording

1. Confirm `GET /health` returns HTTP 200 with `"status": "ok"`,
   `"mcp_connected": true`, matching child `"rag_backend"` and parent
   `"configured_rag_backend"`, `"rag_status_source": "mcp_child"`, and — for dense mode —
   `"dense_encoder_loaded": true`.
2. Run both tasks once and confirm the response says `"planner": "llm"`.
3. Use only the synthetic IDs and prompts below. Do not enter a real name,
   employee ID, complaint, payroll/benefits detail, or a real workplace report.
4. Expand the page's **Retrieved citations** and **MCP tool calls** sections.
   Read the returned trace on screen—the exact query phrasing and number of
   retrieval calls can legitimately differ between LLM runs.
5. Have the current GitHub Actions run and `evaluation/results.md` open in
   separate tabs. Complete the public-host evaluation before claiming its
   results in the presentation.

## Task 1 — PTO: policy retrieval plus employee data

Set employee ID to `E1001` and ask:

> Can I take three days of PTO next week?

Narrate the returned MCP trace in order. The deterministic reference path is:

| MCP tool | Arguments to show | Returned evidence to explain |
| --- | --- | --- |
| `search_policy_documents` | The question (or the LLM's equivalent retrieval query) and the returned `limit` | Cite `pto_policy.md`, especially **Request and Approval**: planned PTO needs at least five calendar days' notice and manager approval |
| `lookup_employee_profile` | `{"employee_id": "E1001"}` | Synthetic profile: manager `Morgan Lee`, New York office/state |
| `check_pto_balance` | `{"employee_id": "E1001"}` | Synthetic PTO balance: `40` available hours, which is five eight-hour days |

Finish by pointing to the final answer and explaining that it combines policy
rules with the synthetic record. State that a real request is not submitted by
this application.

## Task 2 — International remote work: multi-policy retrieval plus profile

Set employee ID to `E1003` and ask:

> I am based in California and want to work from Portugal for six weeks. What approvals and security requirements apply?

Narrate the actual trace. It should include policy retrieval and, because the
question is tied to the synthetic location record, a profile lookup. A live LLM
may call `get_policy_section` after search; show it if it appears rather than
trying to make its tool path match a script.

| MCP tool / evidence | What to explain |
| --- | --- |
| `search_policy_documents` (and possibly `get_policy_section`) | The returned `remote_work_policy.md` **International Work** evidence requires written approval from People Operations, Security, Payroll, and the employee's vice president before working internationally. For a six-week stay, explain the six-week advance request and tax/permanent-establishment review. |
| `search_policy_documents` / data-security citation | Show the returned security policy evidence. Explain only the requirements actually cited by the response, such as company-managed equipment, encryption, MFA, VPN, and no personal-cloud/email copies. |
| `lookup_employee_profile` | Show the exact `{"employee_id": "E1003"}` argument and the synthetic California / San Francisco profile used to make this a structured-data task as well as a RAG task. |

End by connecting the final answer to the citations and saying approval is not
guaranteed; the assistant is reporting fictional policy guidance, not making a
legal, tax, immigration, or employment determination.

## Optional safety and confirmation proof

Only use this synthetic prompt if the presentation needs to show action safety:

> My manager has been harassing me. Please create a mock HR ticket now.

With the confirmation checkbox **off**, show that the answer routes to People
Operations/the reporting channel and that no `mock_action` is created. Then
repeat with the checkbox **on**. Explain that the UI flag becomes
`confirm_mock_action`, the planner only adds `confirmed: true` after that flag,
and the MCP tool independently rejects an unconfirmed direct call. The output is
a deterministic, `mock_only` draft—not a real ticket or investigation. Do not
present a real workplace report in the demo.

## Required quick walkthrough after the tasks

1. **Design:** show the architecture diagram in `design-and-evaluation.md` and
   point out the real stdio MCP boundary, discovered schemas, RAG index, and
   synthetic records.
2. **Deployment:** show the selected Render *or* Railway service, public URL,
   `/health`, host environment-variable screen with values hidden, and
   `deployed.md`. State any measured cold-start note.
3. **CI/CD:** show the GitHub Actions workflow/run. Explain that GitHub Actions
   runs lint, tests, smoke, and an independent MCP protocol check; both hosts
   rerun tests during their build, while Render also waits for passing GitHub
   checks through `autoDeployTrigger: checksPass` before automatic deployment.
4. **Evaluation:** show `evaluation/evaluation_set.json` and
   `evaluation/results.md`. Explain the answer rubrics, complete
   citation/tool coverage, confirmation-action checks, automatic-groundedness
   limitation, and measured public HTTP latency.
5. **AI tooling:** show `ai-tooling.md`, including the Codex first draft, Claude
   second pass, and later verification work.

## Final recording check

- [ ] Each agentic task visibly shows tool name, exact arguments, returned
      result preview, citations, and final answer/action.
- [ ] The live service reports `planner: "llm"` for both primary tasks.
- [ ] No secrets, real personal information, or host billing pages are visible.
- [ ] `deployed.md` has real URLs and the public evaluation result is recorded.
- [ ] The repository has been shared with `quantic-grader`; the required
      submission/Group Project Agreement steps are completed by the group.
