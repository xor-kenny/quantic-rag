"""Build the selected ClearHR policy index before a host starts the web app.

Examples:
    RAG_BACKEND=lexical python scripts/build_rag_index.py
    RAG_BACKEND=dense python scripts/build_rag_index.py

Dense mode downloads/caches FastEmbed's local model under ``data/`` if needed,
then writes a portable dense-vector JSON index.  The running FastAPI parent
never invokes this script; the MCP child owns the one runtime model instance.
"""
# ruff: noqa: E402 -- repository path must be set before application imports.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ``python scripts/build_rag_index.py`` places scripts/, rather than the
# repository root, on sys.path.  Make the documented Render/Railway command
# work from any current directory before importing the application package.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import settings
from app.rag import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("lexical", "dense"),
        default=settings.rag_backend(),
        help="retriever to build (defaults to RAG_BACKEND)",
    )
    args = parser.parse_args()
    index = build_index(args.backend)
    path = settings.DENSE_INDEX_PATH if args.backend == "dense" else settings.INDEX_PATH
    metadata = index["embedding"]
    print(json.dumps({
        "backend": args.backend,
        "chunks": len(index["chunks"]),
        "index": str(path),
        "embedding": metadata,
    }, default=str))


if __name__ == "__main__":
    main()
