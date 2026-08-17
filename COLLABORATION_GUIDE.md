# Safe Collaboration Guide

This repository is designed to be shared with classmates without sharing
accounts, API keys, deployment credentials, or real employee information.

## What classmates can safely receive

Share the GitHub repository URL, not a copy of your whole computer folder:

```text
https://github.com/mehdihamid1/prj.git
```

The committed policy and employee data are fictional coursework data. The app
also runs without an AI key by using its deterministic planner, so a teammate
can clone, test, and develop the project without receiving any secret.

Start here:

1. [README.md](README.md) — install and local-run instructions.
2. [design-and-evaluation.md](design-and-evaluation.md) — architecture, MCP,
   RAG, safety, and evaluation explanation.
3. [OPEN_ITEMS.md](OPEN_ITEMS.md) — work that still needs the deployment owner
   or a live LLM account.

## Safe local setup for each collaborator

```bash
git clone https://github.com/mehdihamid1/prj.git
cd prj
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m pytest -q
uvicorn app.main:app --reload
```

Leave `OPENAI_API_KEY` blank to use the safe, deterministic fallback. It is
enough for development, testing, and the local MCP demo. A teammate who needs to
test the real LLM planner must put **their own** key in their own untracked
`.env` file; they must never paste it into source code, chat, an issue, a pull
request, or a screenshot.

When the LLM planner is enabled, the request text and the tool schemas/results
needed for that turn are transmitted to OpenAI. This is acceptable only for
the synthetic coursework corpus in this repository. Do not enter, paste, or
screen-share real employee information, complaints, health information, payroll
data, credentials, or internal company policy in this demo.

## Access and deployment rules

- Do not share GitHub, OpenAI, Render, or Railway usernames, passwords,
  personal access tokens, or browser sessions.
- Keep one trusted deployment owner. That person enters `OPENAI_API_KEY` only
  in the chosen host's environment-variable settings and controls billing and
  the public deployment.
- **Use `gpt-5.6-luna` on every shared deployment.** It is the committed default,
  chosen to keep the demo cheap. Leave `OPENAI_MODEL` unset on Railway and
  Render so that default applies, or set it explicitly to `gpt-5.6-luna`. A host
  variable silently overrides the repository default, and every request against
  a shared service is billed to the deployment owner — so switching a shared
  deployment to a costlier model spends someone else's money. Test a different
  model in your own `.env` with your own key instead.
- The public demo has a process-local 60-second request guard (defaults: 30 per
  client, 60 total). It limits accidental cost on one instance but is **not**
  authentication, a web-application firewall, or a production privacy control.
  The deployment owner should set an OpenAI spend alert/limit, review host
  logs for unexpected traffic, and take the demo down after grading if it is no
  longer needed.
- Collaborators work on a feature branch and open a pull request. Do not push
  directly to `main`.
- Before merging, require the test suite and a second person’s review. Enable a
  branch-protection rule for `main` when the repository settings allow it.
- Invite only people who need to commit. This repository is under a personal
  GitHub account, where private-repository collaborators have write access. If
  you need read-only reviewers or different roles, move the project to a GitHub
  organization and assign the least-privileged repository roles.

GitHub's current permission documentation confirms the personal-account
write-access limitation and the organization-role alternative: [personal
repository permissions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository),
[organization repository roles](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization).

## What must never be committed or sent to classmates

| Do not share | Safe alternative |
| --- | --- |
| `.env`, API keys, tokens, passwords, cookies, host credentials | `.env.example` with blank values |
| Render/Railway account access or billing details | The deployed public URL and `/health` URL |
| Real employee, PTO, benefits, ticket, customer, or payroll data | The existing synthetic `mock_data/` and policies only |
| Local virtual environments, caches, logs, screenshots containing secrets | A clean `git clone` and the documented commands |

`.env` variants, key/certificate files, virtual environments, caches, logs, and
the generated index are ignored by Git. Do not zip the working folder unless you
first confirm it contains no `.env`, `.venv`, browser downloads, logs, or
deployment exports.

## Before each share or push

Run this short check from the repository root:

```bash
git status --short
git diff --check
git check-ignore .env
git ls-files --error-unmatch .env 2>/dev/null || true
python -m pytest -q
```

Confirm that `.env` is ignored and that no unrecognised file is staged. Do not
use `git add .` until you have reviewed `git status`. Before a real push, also
review the staged diff with `git diff --cached --check` and
`git diff --cached`.

## If a secret is exposed

1. Revoke or rotate the exposed key immediately in its provider account.
2. Remove the key from the current code and stop using any copies of it.
3. Tell every collaborator who may have cloned, forked, or downloaded it.
4. Only then decide whether coordinated Git history cleanup is necessary.

Removing a secret in a later commit does not remove it from existing clones or
forks. GitHub’s guidance is to rotate the credential first and coordinate any
history rewrite with collaborators: [removing sensitive data from a
repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Last safety review — repeat before every share

The following review was recorded on 2026-07-28 across the tracked baseline and
its then-current Git history. It is a useful starting point, not a guarantee for
today's uncommitted work or a future push:

| Check | Result |
| --- | --- |
| Secret-shaped strings (`sk-`, `sk-proj-`, `ghp_`, `github_pat_`, `AKIA…`, `xox…`, private keys) in tracked files | None |
| Same patterns across all commits in history | None |
| `.env` tracked, or present on disk | Neither; `.gitignore` covers it |
| Values assigned in `.env.example` | Only non-secret defaults; `OPENAI_API_KEY` is blank |
| Real personal data in `mock_data/` | None — three invented employees |
| Git history at review time | No secret-shaped strings were found; repeat the scan against the exact commit being shared |

The only matches for "API key" anywhere in the repository are the source lines
that *read* the `OPENAI_API_KEY` environment variable name, and the
documentation telling collaborators to supply their own.

Treat the repository as shareable only after repeating the pre-share check above
on the exact commit you intend to send. This is especially important while
changes are uncommitted: they have not yet been through a reviewable commit or a
remote CI run.
