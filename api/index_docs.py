#!/usr/bin/env python3
"""Index the repository's markdown documentation into Postgres.

    python index_docs.py              # index everything
    python index_docs.py --stats      # what is in the index, no work done
    python index_docs.py --dry-run    # chunk and report, embed nothing
    python index_docs.py ../README.md # one document

Deliberately a separate operation from answering. The API reads the index per
request and holds nothing in memory, so re-indexing after editing a document
takes effect on the next question with no restart.

A document is replaced wholesale rather than diffed: re-indexing deletes its
rows and inserts the new ones, so a deleted section disappears instead of
lingering as an orphan that can still be retrieved.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

import chunking  # noqa: E402
import store  # noqa: E402
from embeddings import MODEL, embed  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Documentation only. Excluded on purpose:
#   .venv, node_modules   third-party docs, thousands of files
#   .pytest_cache         a generated stub that explains nothing
#   api/prompts/*.md      prompts, not documentation - indexing them would let
#                         the model retrieve its own instructions as an answer
EXCLUDE_DIRS = {".git", ".venv", "node_modules", ".next", ".pytest_cache", "__pycache__"}
# A directory prefix, not a list of filenames: enumerating prompts by name
# silently lets the next prompt added leak into the corpus.
EXCLUDE_PREFIXES = ("api/prompts/",)


def documents() -> list[Path]:
    found = []
    for path in sorted(REPO.rglob("*.md")):
        rel = path.relative_to(REPO).as_posix()
        if EXCLUDE_DIRS & set(path.relative_to(REPO).parts):
            continue
        if rel.startswith(EXCLUDE_PREFIXES):
            continue
        found.append(path)
    return found


def index_document(conn, path: Path) -> int:
    """Re-index one document. Returns the number of chunks written.

    Extracted from `main()` so that the agent's `reindex_document` tool and
    this CLI share one implementation - two copies of "how a document is
    indexed" would drift, and the drift would be invisible until a search
    returned something stale.

    Wholesale replacement, like the CLI: the document's rows are deleted and
    re-inserted, so a section removed from the file disappears from the index
    rather than lingering as an orphan. Never prunes - see `main()`.
    """
    rel = path.relative_to(REPO).as_posix()
    chunks = chunking.chunk_markdown(path.read_text(encoding="utf-8"), rel)
    if not chunks:
        return 0
    vectors = embed([c.embed_text for c in chunks])
    rows = [
        {"heading_path": c.heading_path, "ordinal": c.ordinal,
         "text": c.text, "embedding": v}
        for c, v in zip(chunks, vectors)
    ]
    store.replace_document(conn, rel, rows, MODEL)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="documents to index (default: all)")
    ap.add_argument("--stats", action="store_true", help="show the index and exit")
    ap.add_argument("--dry-run", action="store_true", help="chunk only, do not embed or write")
    args = ap.parse_args()

    if args.stats:
        with store.connect() as conn:
            store.ensure_schema(conn)
            s = store.stats(conn)
        print(f"{s['chunks']} chunks from {s['documents']} documents")
        print(f"model: {s['models'] or '-'}   last indexed: {s['indexed_at'] or 'never'}")
        return 0

    paths = [Path(p).resolve() for p in args.paths] if args.paths else documents()
    if not paths:
        print("no markdown found")
        return 1

    conn = None
    if not args.dry_run:
        conn = store.connect()
        store.ensure_schema(conn)

    total = 0
    removed = 0
    try:
        for path in paths:
            rel = path.relative_to(REPO).as_posix()

            if args.dry_run:
                chunks = chunking.chunk_markdown(
                    path.read_text(encoding="utf-8"), rel)
                print(f"  {rel:<34} {len(chunks):>3} chunks"
                      if chunks else f"  {rel:<34} empty, skipped")
                total += len(chunks)
                continue

            written = index_document(conn, path)
            if not written:
                print(f"  {rel:<34} empty, skipped")
                continue
            print(f"  {rel:<34} {written:>3} chunks")
            total += written

        # Only a full run can know what is gone. Indexing a single document
        # says nothing about the others, so pruning there would delete the
        # entire rest of the index.
        if conn and not args.paths:
            keep = [p.relative_to(REPO).as_posix() for p in paths]
            removed = store.prune(conn, keep)
    finally:
        if conn:
            conn.close()

    verb = "would index" if args.dry_run else "indexed"
    print(f"\n{verb} {total} chunks from {len(paths)} documents using {MODEL}")
    if removed:
        print(f"pruned {removed} chunks from documents no longer on disk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
