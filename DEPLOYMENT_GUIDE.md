# Deploy ClearHR on Render or Railway

Choose **one** platform for the final project. Both options deploy the same single FastAPI service and use the configuration already in this repository.

Before starting, push this project to GitHub. Do not commit `.env` files or API keys. The app can run in deterministic fallback mode with no secret, but the graded LLM demo requires `OPENAI_API_KEY` as a host-only environment variable. The committed default is `OPENAI_MODEL=gpt-5.6-luna`; make sure the funded API project behind the key has access to it. In LLM mode, use only the repository's synthetic data because the prompt and relevant tool results are sent to OpenAI.

### RAG backend policy

`RAG_BACKEND=lexical` is the code, local-development, and CI default. It keeps ordinary tests deterministic and avoids an embedding-model download or dense-model memory use. The submitted Render Blueprint intentionally sets `RAG_BACKEND=dense`: its local FastEmbed BGE model downloads about 65 MB of files, and the corresponding Python-3.11 MCP process measured about 293 MB RSS. That choice is supported by a child-owned `/health` check and a three-run deployed evaluation, not merely by the Render environment value or build log.

Railway remains configured and documented as `lexical` because it is not the submitted host and has not completed the same dense resource trial. If Railway becomes the final host, set `RAG_BACKEND=dense` **before** its build, then verify `/health` reports `rag_status_source: "mcp_child"`, matching `rag_backend: "dense"` / `configured_rag_backend: "dense"`, and `dense_encoder_loaded: true`. In every context, changing back to `lexical` is a one-variable rollback.

The committed configuration was checked against the current official [Render
Blueprint reference](https://render.com/docs/blueprint-spec) and [Railway
config-as-code reference](https://docs.railway.com/config-as-code/reference).
Those platforms can change their dashboard wording, so follow the setting names
and commands below rather than relying on an old screenshot.

## Option A — Render

1. Sign in to [Render](https://dashboard.render.com/) and connect the GitHub account that contains this project.
2. Select **New** → **Blueprint**.
3. Select the ClearHR repository and the **`main`** branch to deploy — not a
   feature branch or a pull-request branch. If a service already exists, check
   its configured branch before redeploying; switch it to `main`, save, then
   manually deploy the latest `main` commit. A successful build from another
   branch is not evidence that the submitted source is running.
4. Render reads [render.yaml](render.yaml) automatically. Confirm that it creates one **Web Service** named `clearhr-agentic-hr-assistant`.
5. Confirm these values in the preview:

   | Setting | Value |
   | --- | --- |
   | Runtime | Python |
   | Plan | Free |
   | Build command | `pip install --disable-pip-version-check -r requirements.txt && python scripts/build_rag_index.py` |
   | Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Health check | `/health` |

   `render.yaml` also sets `autoDeployTrigger: checksPass`, so an automatic
   Render deploy waits for the linked branch's GitHub checks. The host build
   therefore does not repeat the test suite or install its development-only
   dependencies. `scripts/build_rag_index.py` inherits the Blueprint's
   `RAG_BACKEND=dense`, so Render builds and serves dense retrieval.

   That gate covers automatic deploys only. The first Blueprint apply and any
   manual **Deploy latest commit** run regardless of GitHub check status, so
   confirm the commit's CI run is green before triggering either by hand.

6. Click **Apply** / **Create Blueprint** and watch the deployment log.
7. In the service's **Environment** settings, add `OPENAI_API_KEY`. Leave `OPENAI_MODEL` unset so the committed `gpt-5.6-luna` default applies, or set it explicitly to `gpt-5.6-luna`. The submitted `render.yaml` pins `RAG_BACKEND=dense` so the build and runtime agree. To roll back, change that Blueprint value to `lexical` and rebuild; do not rely on a conflicting environment-group value. Do not point a shared service at a costlier model: every collaborator's request is billed to the deployment owner's account. Do not put secrets in `render.yaml`, a commit, or a screenshot. The default 60-second demo guard is 30 requests per client and 60 total; adjust its `CHAT_RATE_*` variables only if you understand the cost/privacy trade-off.
8. When the service is live, copy its public `https://...onrender.com` URL. Open `<service-url>/health`; it must return HTTP 200 with `"status": "ok"`, `"mcp_connected": true`, `"rag_status_source": "mcp_child"`, and matching `"rag_backend"` / `"configured_rag_backend"` values. `rag_backend` is reported by the MCP child, not inferred from the web process. The `"commit"` field reports the revision the running process was built from; check it matches the commit you expect to have deployed. An HTTP 503 means the child is unavailable or its backend disagrees with the parent and must be fixed before recording the demo.
9. Open the root URL and submit the PTO demo request: “Can I take three days of PTO next week?” with employee ID `E1001`. Confirm the response reports `planner: "llm"` before treating it as a live-LLM demo.
10. Paste the public app and health URLs into [deployed.md](deployed.md). Note the time of the first request after inactivity as the cold-start observation.

If Render does not detect the Blueprint, create **New** → **Web Service** and enter the four values in the table manually. Keep the runtime as Python. The repository’s `.python-version` pins Python 3.11.

## Option B — Railway

1. Sign in to [Railway](https://railway.app/) and create a **New Project**.
2. Select **Deploy from GitHub repo**, authorize GitHub if prompted, and select the ClearHR repository and the **`main`** branch. Do not use a feature or pull-request branch for the submitted service.
3. Railway reads [railway.toml](railway.toml). In the service’s deployment settings, confirm:

   | Setting | Value |
   | --- | --- |
   | Builder | Railpack |
   | Build command | `pip install -r requirements-dev.txt && python scripts/build_rag_index.py && RAG_BACKEND=lexical python -m pytest -q` |
   | Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Health check path | `/health` |
   | Health check timeout | 60 seconds |

4. In the service's **Variables** settings, add `OPENAI_API_KEY` and set `RAG_BACKEND=lexical`. This is intentional for an unverified Railway deployment: it keeps its build and runtime dependency-light. Leave `OPENAI_MODEL` unset so the committed `gpt-5.6-luna` default applies, or set it explicitly to `gpt-5.6-luna`. If Railway becomes the final host, set `RAG_BACKEND=dense` before rebuilding and complete the child-side `/health` verification in step 7 before claiming dense RAG. Do not point a shared service at a costlier model: every collaborator's request is billed to the deployment owner's account. Do not put secrets in `railway.toml`, a commit, or a screenshot. The default 60-second demo guard is 30 requests per client and 60 total; adjust its `CHAT_RATE_*` variables only if you understand the cost/privacy trade-off.
5. Start the deployment and watch the logs. Do not change `PORT`; Railway supplies it automatically.
6. After a successful deployment, open the service’s **Settings** → **Networking** and choose **Generate Domain**.
7. Open `<railway-domain>/health`. It must return HTTP 200 with `"status": "ok"`, `"mcp_connected": true`, `"rag_status_source": "mcp_child"`, and matching child `"rag_backend"` / parent `"configured_rag_backend"`; HTTP 503 means the MCP child is not usable yet or its backend is mismatched.
8. Open the root URL and run the PTO demo with employee ID `E1001`. Confirm the response reports `planner: "llm"` before recording a live-LLM demo.
9. Paste the public app and health URLs into [deployed.md](deployed.md), including any observed cold-start behavior.

## After either deployment

1. Record the public URLs in `deployed.md` and commit that update.
2. Run the 29-case deployment evaluation from a machine with the repository checked out:

   ```bash
   python -m evaluation.run_eval --base-url https://your-service.example --runs 3 \
     --require-rag-backend dense --deployment-revision <deployed-sha> \
     --deployment-model gpt-5.6-luna
   ```

   This command is correct for the submitted dense Render service. For an intentionally lexical deployment, use `--require-rag-backend lexical` instead. Record the generated median/min–max repeated-run results in `evaluation/results.md`. The artifact retains every run, rather than selecting the best. This is HTTP latency from the evaluation client; separately record one cold request after inactivity in `deployed.md`.
3. Confirm GitHub Actions is green on the deployed branch. Render automatic
   deploys wait for those checks through `autoDeployTrigger: checksPass`;
   Railway's equivalent protection in this repository is its host build command
   running the test suite.
4. Share the repository with GitHub account `quantic-grader`.
5. Use the deployed URL in the recorded demo. For each task, show the returned MCP trace, tool arguments and outputs, citations, and final answer/action.

### Dense-RAG verification and rollback

1. Confirm the build log includes `"backend": "dense"`, then deploy the current revision and open `/health`. It must return HTTP 200 with `"rag_status_source": "mcp_child"`, child `"rag_backend": "dense"`, matching `"configured_rag_backend": "dense"`, and `"dense_encoder_loaded": true`. A build log or a parent-only setting is not sufficient proof. The MCP child must become ready within the host's 60-second health window.
2. Record boot-to-`/health`, first `/chat` after sleep, warm latency, and total service memory if the host exposes it. Run the local retrieval ablation and the three-run public HTTP evaluation after that child-side verification; write those results to the repository before claiming dense improvement.
3. If a health check fails, cold start is impractical, or memory approaches the plan limit, change the selected host's `RAG_BACKEND` value to `lexical` and redeploy. No data migration or code rollback is needed because the two indexes use separate files and preserve the MCP schema.

## If the deployment fails

- **Build failure:** confirm the log shows the selected requirements file and a successful RAG-index build. Render deploys are admitted only after GitHub CI passes; Railway also runs tests during its host build. Do not deploy around a failing test.
- **Health-check failure:** confirm the start command exactly matches this guide and that the check path is `/health`, not `/`. A 503 means the FastAPI process is up but cannot initialize or reach the local MCP child; use the host log to fix that before retrying.
- **Service is up but chat fails:** open `/health` first. If `mcp_connected` is false, inspect the platform logs and redeploy after fixing the logged error.
- **Dense model build/start failure:** set `RAG_BACKEND=lexical` and redeploy to restore the dependency-light index. If the dense build cannot download the public model, check the host's outbound-network log; do not try to commit the model cache or an access token.
- **Slow first request:** this is expected on a free service after inactivity. Record it in `deployed.md` and measure it separately from warm latency.
