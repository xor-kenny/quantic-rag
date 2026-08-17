# Local Dense-RAG Retrieval Comparison

This is a direct retrieval measurement, not a claim about deployed LLM answer
quality. It was generated locally on 2026-07-28 from the versioned 20 cases in
`evaluation/evaluation_set.json` that declare expected policy documents (28
expected document labels total), with the same 142 heading-aware chunks and
`TOP_K=4`.

Commands used:

```bash
RAG_BACKEND=lexical python -c "from evaluation.run_eval import load_cases, retrieval_ablation; print(retrieval_ablation(load_cases()))"
RAG_BACKEND=dense python -c "from evaluation.run_eval import load_cases, retrieval_ablation; print(retrieval_ablation(load_cases()))"
```

Dense mode used `fastembed==0.8.0` with the local
`BAAI/bge-small-en-v1.5` 384-dimensional model and the persisted
`data/index.dense.json` vector index. Lexical mode used the existing
IDF-weighted feature-hash JSON index. The evaluator deduplicates document names
when calculating precision and complete coverage, so repeated chunks from one
document do not inflate a score.

| Top-k | Backend | Any-doc recall | Expected-doc recall | Complete required-doc coverage | Document precision | MRR |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | lexical | 15/20 (75%) | 15/28 (54%) | 10/20 (50%) | 15/20 (75%) | .750 |
| 1 | dense | 16/20 (80%) | 16/28 (57%) | 11/20 (55%) | 16/20 (80%) | .800 |
| 2 | lexical | 16/20 (80%) | 17/28 (61%) | 12/20 (60%) | 17/34 (50%) | .775 |
| 2 | dense | 19/20 (95%) | 20/28 (71%) | 14/20 (70%) | 20/28 (71%) | .875 |
| 4 | lexical | 16/20 (80%) | 18/28 (64%) | 12/20 (60%) | 18/60 (30%) | .775 |
| 4 | dense | 19/20 (95%) | 23/28 (82%) | 16/20 (80%) | 23/43 (53%) | .875 |
| 6 | lexical | 16/20 (80%) | 20/28 (71%) | 12/20 (60%) | 20/84 (24%) | .775 |
| 6 | dense | 19/20 (95%) | 25/28 (89%) | 17/20 (85%) | 25/58 (43%) | .875 |
| 8 | lexical | 16/20 (80%) | 22/28 (79%) | 14/20 (70%) | 22/105 (21%) | .775 |
| 8 | dense | 19/20 (95%) | 25/28 (89%) | 17/20 (85%) | 25/66 (38%) | .875 |

At the default `TOP_K=4`, dense retrieval improves expected-document recall by
18 percentage points, complete multi-document coverage by 20 points, document
precision by 23 points, and MRR by .100 on this fixed local set.

## Resource observation

The dense index itself is about 1.4 MB and the cached model files are about
65 MB. In the Python 3.11 environment that matches deployment, a local
one-process `ensure_ready()` plus one query measured 292,932 KB maximum RSS and
0.99 s after the model had been cached; lexical measured 16,344 KB and 0.05 s
in the earlier local baseline. The dense model is deliberately loaded only in
the MCP child, not in the FastAPI parent.

These figures are not a Render/Railway guarantee. Before setting
`RAG_BACKEND=dense` on the submitted host, measure boot-to-`/health`, parent +
child RSS, one cold request after sleep, and the 29-case public HTTP evaluation.
Keep `RAG_BACKEND=lexical` as the immediate rollback path if any host limit is
approached.
