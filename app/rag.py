"""Local RAG index with transparent citations and a reversible dense backend.

Three source formats are supported — Markdown, HTML, and plain text — because the
policy corpus is authored in the format that suits each document. All three are
parsed heading-aware so that every chunk carries the section heading it came
from, which is what makes a citation useful to a reader.

The default lexical backend is deliberately deterministic and dependency-light.
The optional dense backend uses FastEmbed's local BGE-small ONNX model and a
persisted 384-dimensional vector index.  Both retain the same chunk IDs and MCP
output shape, so a host can roll back with ``RAG_BACKEND=lexical`` without
changing the planner, citations, or mock-data tools.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import settings
from .settings import POLICY_DIR

# A large hashing space keeps collisions rare across the full corpus. Vectors are
# stored sparsely (bucket -> weight), so dimensionality costs nothing on disk.
DIMENSIONS = 2 ** 18
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9'-]{1,}")

# Document title and section heading are strong topical signals. They are counted
# extra so that a query such as "security requirements" can retrieve a relevant
# section even when the body uses more specific wording than the query.
HEADING_WEIGHT = 3

# A token is "distinctive" when it is rare enough across the corpus to carry
# topic information. Corpus-wide filler ("the", "employee", "policy") falls below.
DISTINCTIVE_IDF_FLOOR = 2.0

# Chunking is a fixed word window with overlap so the index is byte-reproducible.
CHUNK_WORDS = 220
CHUNK_STRIDE = 190

SUPPORTED_SUFFIXES = (".md", ".html", ".txt")

# A plain-text heading is a short, fully upper-case line — the convention used by
# the .txt documents in the corpus.
TXT_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 ,.'&/()-]{3,}$")


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _bucket(token: str) -> int:
    return int(hashlib.sha256(token.encode()).hexdigest(), 16) % DIMENSIONS


def build_idf(texts: list[str]) -> dict[str, float]:
    """Inverse document frequency per token, keyed by hash bucket.

    Without this weighting, tokens that appear in nearly every policy document
    ("employee", "policy", "company") dominate every vector and retrieval
    collapses towards whichever chunk is longest. Storing the weights in the
    index keeps query-time and index-time scoring identical.
    """
    total = len(texts) or 1
    frequency: Counter[int] = Counter()
    for text in texts:
        frequency.update({_bucket(token) for token in _tokens(text)})
    return {str(bucket): math.log((total + 1) / (count + 1)) + 1.0 for bucket, count in frequency.items()}


def embed(text: str, idf: dict[str, float] | None = None, heading: str = "") -> dict[str, float]:
    """Return a stable IDF-weighted sparse feature-hash vector.

    Returned as a ``{bucket: weight}`` mapping of non-zero components only. No
    network access and no API key are required, and the result is byte-identical
    across runs and machines.
    """
    counts = Counter(_tokens(text))
    if heading:
        for token in _tokens(heading):
            counts[token] += HEADING_WEIGHT

    vector: dict[str, float] = {}
    for token, count in counts.items():
        bucket = str(_bucket(token))
        weight = 1.0 if idf is None else idf.get(bucket, 1.0)
        vector[bucket] = vector.get(bucket, 0.0) + (1 + math.log(count)) * weight

    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    return {bucket: round(value / norm, 6) for bucket, value in vector.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    """Dot product of two L2-normalised sparse vectors."""
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(bucket, 0.0) for bucket, value in left.items())


def dense_cosine(left: list[float], right: list[float]) -> float:
    """Dot product of L2-normalised dense vectors without a NumPy dependency.

    The corpus is only 142 chunks, so brute-force cosine is much faster than an
    HTTP/MCP round trip and avoids introducing a second database service merely
    to search a few hundred kilobytes of vectors.
    """
    if len(left) != len(right):
        raise ValueError("Dense vectors must have the same dimension.")
    return sum(a * b for a, b in zip(left, right))


def _normalise_dense(vector: object) -> list[float]:
    """Convert a FastEmbed/fixture vector to a portable L2-normalised list."""
    values = [float(value) for value in vector]  # type: ignore[arg-type]
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        raise ValueError("Embedding model returned a zero vector.")
    return [value / norm for value in values]


_dense_encoder: Any | None = None


def _get_dense_encoder() -> Any:
    """Create the local ONNX encoder lazily, only in the dense RAG process."""
    global _dense_encoder
    if _dense_encoder is None:
        try:
            from fastembed import TextEmbedding
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Dense RAG requires fastembed. Install requirements.txt or set RAG_BACKEND=lexical."
            ) from exc

        settings.RAG_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _dense_encoder = TextEmbedding(
            model_name=settings.RAG_MODEL,
            cache_dir=str(settings.RAG_MODEL_CACHE_DIR),
        )
    return _dense_encoder


def _encode_dense(texts: list[str], encoder: Any | None = None) -> list[list[float]]:
    """Embed a batch and normalise its vectors for portable cosine scoring."""
    active_encoder = encoder or _get_dense_encoder()
    vectors = [_normalise_dense(vector) for vector in active_encoder.embed(texts)]
    if len(vectors) != len(texts):
        raise RuntimeError("Embedding model returned a different number of vectors than texts.")
    return vectors


def _passage_for_embedding(chunk: dict[str, str]) -> str:
    """Make document and section metadata part of each semantic passage."""
    return f"Document: {chunk['title']}. Section: {chunk['section']}. {chunk['text']}"


def _query_for_embedding(query: str) -> str:
    """Use BGE's recommended short-query retrieval instruction only for queries."""
    return f"{settings.RAG_QUERY_INSTRUCTION}{query}"


def distinctive_tokens(text: str, idf: dict[str, float] | None, floor: float = DISTINCTIVE_IDF_FLOOR) -> set[str]:
    """Query tokens carrying real topical signal, i.e. not corpus-wide filler.

    Cosine similarity alone cannot separate an in-corpus question from an
    out-of-corpus one: a short off-topic question normalises to a small vector
    that can still align with a long chunk by accident. Counting how many of the
    caller's *distinctive* words actually occur in a chunk is a far cleaner
    signal, and it is what the refusal guardrail keys on.
    """
    if idf is None:
        return set(_tokens(text))
    # A token the corpus has never seen is maximally distinctive, so it defaults
    # to infinity rather than zero — it is precisely the evidence that a question
    # is out of scope, and must not be filtered out of the check.
    return {token for token in _tokens(text) if idf.get(str(_bucket(token)), math.inf) >= floor}


class _HeadingAwareHTMLParser(HTMLParser):
    """Collect HTML body text grouped under the most recent heading."""

    SKIP = {"script", "style", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._section = "Overview"
        self._buffer: list[str] = []
        self._in_heading = False
        self._heading: list[str] = []
        self._skip_depth = 0

    def _flush(self) -> None:
        text = " ".join(" ".join(self._buffer).split())
        if text:
            self.blocks.append((self._section, text))
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._in_heading = True
            self._heading = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif re.fullmatch(r"h[1-6]", tag):
            heading = " ".join(" ".join(self._heading).split())
            if heading:
                self._section = heading
            self._in_heading = False
        elif tag in {"td", "th", "p", "li", "tr"}:
            # Keep cell and list boundaries from fusing into one long word run.
            self._buffer.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_heading:
            self._heading.append(data)
        else:
            self._buffer.append(data)

    def close(self) -> None:  # noqa: D102 - inherited behaviour plus a final flush
        super().close()
        self._flush()


def _blocks_from_markdown(text: str) -> list[tuple[str, str]]:
    section = "Overview"
    buffer: list[str] = []
    blocks: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if buffer:
                blocks.append((section, "\n".join(buffer).strip()))
                buffer = []
            section = line.lstrip("# ").strip()
        else:
            buffer.append(line)
    if buffer:
        blocks.append((section, "\n".join(buffer).strip()))
    return [(heading, body) for heading, body in blocks if body]


def _blocks_from_html(text: str) -> list[tuple[str, str]]:
    parser = _HeadingAwareHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.blocks


def _blocks_from_text(text: str) -> list[tuple[str, str]]:
    section = "Overview"
    buffer: list[str] = []
    blocks: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and TXT_HEADING_RE.fullmatch(stripped):
            if buffer:
                blocks.append((section, "\n".join(buffer).strip()))
                buffer = []
            section = stripped.title()
        else:
            buffer.append(line)
    if buffer:
        blocks.append((section, "\n".join(buffer).strip()))
    return [(heading, body) for heading, body in blocks if body]


_PARSERS = {
    ".md": _blocks_from_markdown,
    ".html": _blocks_from_html,
    ".txt": _blocks_from_text,
}


def parse_document(path: Path) -> list[dict[str, str]]:
    """Return heading-aware, window-limited chunks for one policy document."""
    blocks = _PARSERS[path.suffix](path.read_text(encoding="utf-8"))
    title = path.stem.replace("_", " ").title()

    chunks: list[dict[str, str]] = []
    for section, body in blocks:
        words = body.split()
        for start in range(0, max(len(words), 1), CHUNK_STRIDE):
            part = words[start : start + CHUNK_WORDS]
            if part:
                chunks.append({"title": title, "section": section, "text": " ".join(part)})
            if start + CHUNK_WORDS >= len(words):
                break
    return chunks


def source_documents() -> list[Path]:
    """Every indexable policy file, in a stable order."""
    return sorted(p for p in POLICY_DIR.iterdir() if p.suffix in SUPPORTED_SUFFIXES)


def _backend_name(backend: str | None = None) -> str:
    chosen = settings.rag_backend() if backend is None else backend.strip().lower()
    if chosen not in {"lexical", "dense"}:
        raise ValueError("RAG backend must be either 'lexical' or 'dense'.")
    return chosen


def _index_path(backend: str) -> Path:
    return settings.DENSE_INDEX_PATH if backend == "dense" else settings.INDEX_PATH


def _current_index(index: dict[str, Any], backend: str) -> bool:
    """Ensure a stale/other-backend index is never silently reused."""
    if index.get("version") != settings.RAG_INDEX_VERSION or index.get("backend") != backend:
        return False
    if backend == "dense":
        metadata = index.get("embedding")
        return (
            isinstance(metadata, dict)
            and metadata.get("provider") == "fastembed"
            and metadata.get("model") == settings.RAG_MODEL
            and isinstance(metadata.get("dimensions"), int)
        )
    return index.get("embedding") == "sparse-hash-idf+document-metadata"


def build_index(backend: str | None = None, *, encoder: Any | None = None) -> dict[str, Any]:
    """Build one versioned local index without changing the other backend.

    Passing a small fake ``encoder`` keeps the dense-index contract testable in
    CI without downloading a model. Production uses the cached FastEmbed model.
    """
    backend_name = _backend_name(backend)

    # First pass: chunk every document so IDF is computed over the real corpus.
    staged: list[dict[str, str]] = []
    for path in source_documents():
        for number, chunk in enumerate(parse_document(path), start=1):
            staged.append({
                "id": f"{path.stem}-{number}", "document": path.name,
                "format": path.suffix.lstrip("."), **chunk,
            })

    # Keep lexical IDF metadata for the scope guard even when semantic vectors
    # rank passages. Dense similarity alone cannot reliably refuse a query that
    # is unrelated to every company policy.
    idf = build_idf([
        f"{chunk['document']} {chunk['title']} {chunk['section']} {chunk['text']}"
        for chunk in staged
    ])

    if backend_name == "lexical":
        records = [
            {
                **chunk,
                "embedding": embed(
                    chunk["text"],
                    idf,
                    heading=f"{chunk['document']} {chunk['title']} {chunk['section']}",
                ),
            }
            for chunk in staged
        ]
        index: dict[str, Any] = {
            "version": settings.RAG_INDEX_VERSION,
            "backend": backend_name,
            "embedding": "sparse-hash-idf+document-metadata",
            "idf": idf,
            "chunks": records,
        }
    else:
        vectors = _encode_dense([_passage_for_embedding(chunk) for chunk in staged], encoder)
        if not vectors:
            raise RuntimeError("Cannot build a dense index with no policy chunks.")
        records = [{**chunk, "embedding": vector} for chunk, vector in zip(staged, vectors)]
        index = {
            "version": settings.RAG_INDEX_VERSION,
            "backend": backend_name,
            "embedding": {
                "provider": "fastembed",
                "model": settings.RAG_MODEL,
                "dimensions": len(vectors[0]),
                "query_instruction": settings.RAG_QUERY_INSTRUCTION,
                "storage": "local-json-dense-vector-index",
            },
            "idf": idf,
            "chunks": records,
        }

    path = _index_path(backend_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index), encoding="utf-8")
    return index


def load_index(backend: str | None = None) -> dict[str, Any]:
    """Load a current index or rebuild only the selected backend."""
    backend_name = _backend_name(backend)
    path = _index_path(backend_name)
    if path.exists():
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}
        if _current_index(index, backend_name):
            return index
    return build_index(backend_name)


def ensure_ready() -> dict[str, Any]:
    """Load the selected index and warm the dense encoder in the caller process."""
    backend_name = _backend_name()
    index = load_index(backend_name)
    if backend_name == "dense":
        _get_dense_encoder()
    return index


def runtime_status(index: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return safe, child-owned facts about the effective retrieval runtime.

    This function is used by the MCP diagnostic tool after ``ensure_ready()``.
    It deliberately reports the selected backend, the loaded index metadata,
    and whether the dense encoder lives in *this* process. It never returns
    policy text, embeddings, environment variables, or provider credentials.
    """
    backend = _backend_name()
    active_index = index or load_index(backend)
    index_backend = active_index.get("backend")
    if index_backend != backend:
        raise RuntimeError("Loaded RAG index backend does not match the configured backend.")

    embedding = active_index.get("embedding")
    if backend == "dense":
        if not isinstance(embedding, dict):
            raise RuntimeError("Dense RAG index is missing embedding metadata.")
        model = embedding.get("model")
        dimensions = embedding.get("dimensions")
        provider = embedding.get("provider")
        storage = embedding.get("storage")
    else:
        model = "sparse-hash-idf"
        dimensions = DIMENSIONS
        provider = "local"
        storage = embedding

    return {
        "rag_backend": backend,
        "index_backend": index_backend,
        "rag_index": _index_path(backend).name,
        "rag_index_version": active_index.get("version"),
        "rag_chunks": len(active_index.get("chunks", [])),
        "rag_model": model,
        "rag_dimensions": dimensions,
        "rag_provider": provider,
        "rag_storage": storage,
        "dense_encoder_loaded": _dense_encoder is not None if backend == "dense" else False,
    }


def _lexical_support(item: dict[str, Any], wanted: set[str]) -> float:
    present = wanted & set(_tokens(
        f"{item['document']} {item['title']} {item['section']} {item['text']}"
    ))
    return len(present) / len(wanted) if wanted else 0.0


def search(query: str, limit: int = 4, *, backend: str | None = None) -> list[dict[str, Any]]:
    """Return citable policy chunks ranked by the configured vector backend.

    ``support`` is per-result lexical evidence. ``query_support`` is the best
    lexical support anywhere in the corpus and is retained as a conservative
    out-of-corpus guard when dense semantic ranking is active.
    """
    if limit <= 0:
        return []
    backend_name = _backend_name(backend)
    index = load_index(backend_name)
    idf = index.get("idf")
    wanted = distinctive_tokens(query, idf)

    if backend_name == "dense":
        query_vector = _encode_dense([_query_for_embedding(query)])[0]
    else:
        query_vector = embed(query, idf)

    matches: list[dict[str, Any]] = []
    for item in index["chunks"]:
        support = _lexical_support(item, wanted)
        if backend_name == "dense":
            score = dense_cosine(query_vector, item["embedding"])
        else:
            score = cosine(query_vector, item["embedding"])
        matches.append(
            {key: value for key, value in item.items() if key != "embedding"}
            | {"score": round(score, 3), "support": round(support, 3)}
        )

    query_support = max((match["support"] for match in matches), default=0.0)
    for match in matches:
        match["query_support"] = query_support

    if backend_name == "dense":
        ordered = sorted(matches, key=lambda result: (-result["score"], -result["support"], result["id"]))
    else:
        ordered = sorted(matches, key=lambda result: (-result["support"], -result["score"], result["id"]))

    # One strong chunk from each distinct document before a second chunk from
    # any of them. A long policy otherwise takes every slot: the question
    # "working from Portugal — what approvals and security requirements apply?"
    # returned four remote-work chunks, two of them the same section, and the
    # data-security policy that answers the second half of the question was
    # never retrieved at all. The planner can still read more of a chosen
    # document with get_policy_section.
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    seen_documents: set[str] = set()
    for result in ordered:
        if result["document"] in seen_documents:
            continue
        selected.append(result)
        selected_ids.add(result["id"])
        seen_documents.add(result["document"])
        if len(selected) == limit:
            return selected

    # A corpus smaller than the requested limit keeps ordinary top-k behaviour
    # rather than returning fewer results than asked for.
    for result in ordered:
        if result["id"] in selected_ids:
            continue
        selected.append(result)
        if len(selected) == limit:
            break
    return selected
