# Synthetic Mock Data

Every record in this directory is fictional. There is no real employee
information here, and none of it is derived from a real person or organisation.

| File | Records | Contents |
| --- | --- | --- |
| `employees.json` | 3 | Employee ID, name, employment type, role, manager, home state, office |
| `pto_balances.json` | 3 | Available PTO hours and accrual rate per pay period |
| `benefits.json` | 3 | Enrolment status and elected plans |

The three employees are shaped to exercise different branches of the policy
corpus, which is why the evaluation set keeps returning to them:

| ID | Profile | What it tests |
| --- | --- | --- |
| `E1001` | Full-time, New York, 40 PTO hours, benefits active | The ordinary path: sufficient balance, benefits eligible |
| `E1002` | Part-time, remote, 12 PTO hours, **not** benefits eligible | Ineligibility and insufficient-balance handling |
| `E1003` | Full-time, California / San Francisco, 72 PTO hours | Cross-state remote work approval questions |

Records are read through `app/data.py`, which is called only by the MCP tools in
`app/mcp_server.py`. Nothing in the agent or the web layer reads these files
directly — see [../mcp/README.md](../mcp/README.md) for why that boundary matters.

Tickets created by `create_mock_hr_ticket` are never written here. The tool
returns a draft object and modifies no file, so no state accumulates across runs.
