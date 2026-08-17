# AI Tooling Disclosure

Two AI coding tools were used, in sequence, with a deliberate division of labour: Codex produced the first draft, Claude Code did the second pass. Roughly all of the code in this repository was AI-generated; all of it was reviewed, and a material amount of it was corrected.

## Codex — first draft

Codex created the initial scaffold in one pass: FastAPI endpoints (`/`, `/chat`, `/health`, `/tools`), the local RAG implementation, an initial synthetic policy corpus, the mock employee data, six FastMCP tool definitions, the deterministic planner, the first tests, the CI workflow, and the deployment configuration for Render and Railway.

**What it did well.** The architecture was sound on the first attempt and did not need rework. In particular Codex got the MCP boundary right — the orchestrator called tools through `mcp_client`, never importing `app.rag` or `app.data` directly — which is the single thing this project is most easily marked down for. It also produced a coherent, consistent project structure with matching documentation, and the `render.yaml` / `railway.toml` pair worked without modification.

**Where it fell short.** Everything it produced was structurally correct but under-scaled. The corpus was six documents totalling 606 words against a brief asking for 30–120 pages — the shape was right, the volume was off by a factor of about 25. The evaluation set had six items against a required 20–30. Both read as placeholders that a reader could mistake for finished work, which is the failure mode worth watching for.

## Claude Code — second pass

Claude Code was given the project brief and asked to audit the draft against it, then apply fixes. The audit found 18 issues; the substantive ones were:

| Area | Change |
| --- | --- |
| Corpus | 6 documents / 606 words → 14 documents / 15,969 words, and Markdown-only → Markdown, HTML, and plain text, to meet the two-format requirement |
| Ingestion | Added heading-aware HTML and plain-text parsers alongside the Markdown one |
| Retrieval | Rewrote the embedding after the larger corpus broke it (see below) |
| Planner | Added the LLM tool-use loop; the draft had no LLM at all |
| Guardrails | Added the retrieval-support signal behind the out-of-corpus refusal |
| Evaluation | 6 → 29 cases, plus a harness that measures answer rubrics, citation/tool coverage, confirmation, and latency rather than estimates |
| CI | Added lint, index build, and a stdio MCP discovery-and-call check |
| Structure | Moved the synthetic records into `mock_data/` as the brief specifies |

**Two things it found that were not on the checklist.** Expanding the corpus 25× silently broke retrieval: the original 256-dimension unweighted hash embedding collapsed under hash collisions and corpus-wide vocabulary, dropping top-1 document accuracy to 3/13 on a probe set. Diagnosing that required measuring, not reading. The fix — IDF weighting, a larger sparse hashing space, and heading weighting — brought it to 10/13 top-1 and 13/13 top-3.

Separately, the first attempt at the out-of-corpus guardrail had an inverted default: an unseen token got an IDF of 0.0 and was therefore *excluded* from the very check meant to catch it, so *"What is the capital of France?"* scored a perfect 1.00 support. The bug produced plausible-looking numbers, which is what made it worth noting.

**Where it needed correction.** It wrote a technology-stack diagram containing three claims that were not true of the repository: `ruff` shown running in CI when it was not installed anywhere, a GitHub Actions deploy hook that did not exist, and a caption describing a dashed-box convention the diagram did not use. These were caught on a later review pass and fixed — either by correcting the document or, in ruff's case, by making the claim true. The general lesson is that a generated diagram will confidently describe the system the model has in mind rather than the one on disk.

## What worked well overall

The hand-off worked. Codex is fast at producing a complete, coherent skeleton, and having the architecture settled made the second pass a matter of deepening rather than restructuring. Reviewing a second model's output is also markedly easier than reviewing your own: the gaps in the first draft were obvious to the second tool in a way they might not have been to the author.

The MCP boundary and the automated tests both paid for themselves. Because tool access was already funnelled through one function, adding an LLM planner touched no code below that line.

## What required human review

- **Measured numbers.** Every metric in `evaluation/results.md` comes from a run of `evaluation/run_eval.py`. Nothing was written by hand, and the harness labels its groundedness metric as an automatic proxy rather than presenting it as the real measurement.
- **Live paths are labelled with their actual evidence.** The first planner pass had no API key, so its loop was initially covered only by a stub client. A later 29-case Render HTTP run produced live `planner: "llm"` artifacts alongside deterministic safety-gate cases; it is recorded in `evaluation/results.md` rather than described as a perfect or all-LLM result. Any later planner or guardrail change requires a new deployed run, and the response alone does not prove a host's exact model override or commit.
- **Volume claims.** "About 30 pages" is an estimate from word count at roughly 500 words per page, not a rendered page count.
- **Policy content.** All fourteen documents are fictional and were checked to make sure nothing reads as advice about a real organisation.
- **Secrets.** `.env` is git-ignored, `.env.example` carries no value, and no key appears in any committed file.

## Later Codex verification pass

Codex performed a later deployment-and-safety review after the Claude pass. It
added a safe unavailable health response, bounded HTTP/MCP/provider waits,
process-local public-demo rate limits, explicit UI confirmation, tool-boundary
confirmation enforcement, identity binding for LLM record lookups, redacted
sensitive escalation traces, and a stronger evaluator with deployed-HTTP mode.
The pass also corrected the documentation and added the presentation runbook.

After the first live evaluation exposed that API-key deployments bypassed some
fallback-only guards, Codex moved clarification, scope, jurisdiction, and
lost-device incident controls ahead of the LLM. It also added explicit MCP tool
capability authorisation, a total tool-call budget, and tests for those paths.

**What worked well.** A focused audit against the actual running paths exposed
issues that a structural review missed: a health endpoint that could return 200
while MCP was unavailable, an evaluator that did not use its gold answers, and
a model path that relied too heavily on prompt-only grounding.

**What did not work perfectly.** The restricted local development sandbox can
block the stdio child process used by real MCP tests. Validation therefore used
the same normal-host process model expected by Render/Railway for MCP lifecycle
checks; it must still be rerun in GitHub Actions and on the selected host before
submission.

## Dense-RAG verification pass

Codex then implemented the previously deferred semantic retrieval option rather
than continuing to describe it as future work. It added a pinned local
FastEmbed/BGE encoder, a versioned dense-vector index, a lexical feature flag
for immediate rollback, model warm-up in the MCP child only, host-build index
creation, dense contract tests that do not download a model in CI, and stricter
retrieval-ablation metrics. It also measured the real model rather than
guessing from its file size: the 142-vector index is small, but the local
Python-3.11 ONNX process reached about 293 MB RSS. That changed the deployment recommendation
from “enable dense by default” to “run an explicit host trial first.” The
comparison and its limits are recorded in
[`evaluation/dense_rag_comparison.md`](evaluation/dense_rag_comparison.md) and
`OPEN_ITEMS.md`.

**What worked well.** Keeping the lexical representation behind a feature flag
made a material architecture change reversible, while mocked encoder tests kept
CI deterministic and independent of model downloads.

**What required human review.** The final free-tier choice cannot be made from
a laptop RSS number alone. Render/Railway must measure total parent-plus-child
memory, cold boot, and the public LLM evaluation before dense RAG is presented
as the submitted deployment configuration.
