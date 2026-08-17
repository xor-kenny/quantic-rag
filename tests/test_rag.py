import json

import pytest

from app import rag, settings
from app.rag import build_index, search


def test_policy_search_returns_citable_pto_evidence():
    build_index()
    result = search("How much notice is needed for PTO?", 3)
    assert result
    assert any(item["document"] == "pto_policy.md" for item in result)
    assert all({"id", "section", "text"} <= item.keys() for item in result)


def test_policy_search_respects_an_empty_requested_limit():
    assert search("PTO", 0) == []


class _SemanticFixtureEncoder:
    """Small deterministic stand-in so CI never downloads an embedding model."""

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def embed(self, texts: list[str]):
        self.inputs.append(texts)
        for text in texts:
            normalized = text.lower()
            # "leave bank" is a semantic stand-in for the PTO policy's
            # "single combined bank" wording.  None of the dense ranking
            # assertions rely on the lexical hasher's score/order.
            if "leave bank" in normalized or "single combined bank" in normalized:
                yield [4.0, 0.0, 0.0]
            elif "security" in normalized or "device" in normalized:
                yield [0.0, 4.0, 0.0]
            else:
                yield [0.0, 0.0, 4.0]


def test_dense_index_ranks_semantic_fixture_and_preserves_citation_contract(monkeypatch, tmp_path):
    """Dense vectors are versioned, normalised, and never exposed to callers."""
    encoder = _SemanticFixtureEncoder()
    dense_path = tmp_path / "index.dense.json"
    monkeypatch.setattr(settings, "DENSE_INDEX_PATH", dense_path)
    monkeypatch.setattr(settings, "RAG_BACKEND", "dense")
    monkeypatch.setattr(rag, "_get_dense_encoder", lambda: encoder)

    index = build_index("dense", encoder=encoder)
    results = search("How do I use my leave bank?", 3)

    assert index["backend"] == "dense"
    assert index["embedding"]["provider"] == "fastembed"
    assert index["embedding"]["dimensions"] == 3
    assert dense_path.exists()
    assert results[0]["document"] == "pto_policy.md"
    assert results[0]["score"] == pytest.approx(1.0)
    assert results[0]["query_support"] >= results[0]["support"]
    assert all("embedding" not in result for result in results)
    # BGE uses the retrieval instruction on short queries, never on passages.
    assert encoder.inputs[-1][0].startswith(settings.RAG_QUERY_INSTRUCTION)


def test_dense_stale_index_is_rebuilt_without_touching_lexical_index(monkeypatch, tmp_path):
    encoder = _SemanticFixtureEncoder()
    dense_path = tmp_path / "index.dense.json"
    lexical_path = tmp_path / "index.lexical.json"
    monkeypatch.setattr(settings, "DENSE_INDEX_PATH", dense_path)
    monkeypatch.setattr(settings, "INDEX_PATH", lexical_path)
    monkeypatch.setattr(rag, "_get_dense_encoder", lambda: encoder)

    build_index("dense", encoder=encoder)
    stale = json.loads(dense_path.read_text())
    stale["version"] = 0
    dense_path.write_text(json.dumps(stale))
    rebuilt = rag.load_index("dense")

    assert rebuilt["version"] == settings.RAG_INDEX_VERSION
    assert rebuilt["backend"] == "dense"
    assert not lexical_path.exists(), "rebuilding dense must not overwrite lexical rollback data"


def test_invalid_backend_fails_clearly(monkeypatch):
    monkeypatch.setattr(settings, "RAG_BACKEND", "not-a-retriever")
    with pytest.raises(ValueError, match="RAG_BACKEND"):
        rag.ensure_ready()
