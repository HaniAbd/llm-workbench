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

# Directories whose contents are third-party or generated, wherever they turn
# up in the tree. A name rather than a path, because `.venv` is `.venv` at any
# depth; they share one reason, which is why they are a set and not the mapping
# below.
EXCLUDE_DIRS = {".git", ".venv", "node_modules", ".next", ".pytest_cache", "__pycache__"}

# Paths kept out of the corpus, each with the reason it is kept out.
#
# A mapping rather than a list, because the reason is the valuable half: it is
# what reaches whoever asked for the file, and the only record of why the rule
# exists. Adding an exclusion is therefore one line of data here and no change
# to `_rejection` - which is the property worth having, since the second
# exclusion is where a rule usually becomes a special case and the third is
# where it becomes an argument.
#
# A pattern ending in "/" is a directory prefix; anything else is an exact
# path. Prompts are a prefix on purpose - naming them individually would let
# the next prompt added leak into the corpus - while a generated file is one
# named file, and matching it as a prefix would also swallow, say, a
# `web/CLAUDE.md.bak` that nobody excluded.
EXCLUDED_PATHS = {
    "api/prompts/": (
        "a prompt, not documentation - indexing it would let the model "
        "retrieve its own instructions as an answer"
    ),
    "web/AGENTS.md": (
        "written by `next dev` and re-added whenever it runs - it describes "
        "Next.js in general, not this project"
    ),
    "web/CLAUDE.md": (
        "a one-line pointer to web/AGENTS.md, with no content of its own"
    ),
}


def _matches(rel: str, pattern: str) -> bool:
    """Directory prefix if the pattern ends in "/", otherwise an exact path."""
    return rel.startswith(pattern) if pattern.endswith("/") else rel == pattern


class NotIndexable(ValueError):
    """This path must not become part of the document corpus.

    Raised rather than returned, and raised rather than skipped: a caller that
    forgets to check gets an error, and a caller that asked for something
    excluded is told so instead of being quietly given nothing.
    """


def _rejection(resolved: Path) -> str | None:
    """Why `resolved` may not be indexed, or None if it may be.

    The single definition of what belongs in the corpus. `documents()` filters
    with it and `index_document()` enforces it, which is the whole point: the
    exclusion used to live in `documents()` alone, so naming a prompt file
    explicitly walked straight past it and indexed the model's own
    instructions. A rule that only applies on the path someone remembered to
    guard is not a rule.

    Takes an already-absolute path and does no I/O beyond what the caller has
    done, so it is cheap enough to run over every file in the tree.
    """
    # First, because everything below reads a repository-relative path.
    if not resolved.is_relative_to(REPO):
        return "outside the repository"
    rel = resolved.relative_to(REPO)
    if resolved.suffix != ".md":
        return "not a markdown file"
    if EXCLUDE_DIRS & set(rel.parts):
        return "inside an excluded directory"
    posix = rel.as_posix()
    for pattern, reason in EXCLUDED_PATHS.items():
        if _matches(posix, pattern):
            return reason
    return None


def check_indexable(path: Path) -> Path:
    """Resolve `path` and confirm it may be indexed, or raise `NotIndexable`."""
    resolved = Path(path).resolve()
    reason = _rejection(resolved)
    if reason is not None:
        raise NotIndexable(f"{path}: {reason}")
    if not resolved.is_file():
        raise NotIndexable(f"{path}: not a file")
    return resolved


def documents() -> list[Path]:
    """Every document the corpus is allowed to contain."""
    return [path for path in sorted(REPO.rglob("*.md"))
            if _rejection(path) is None]


def index_document(conn, path: Path) -> int:
    """Re-index one document. Returns the number of chunks written.

    Extracted from `main()` so that the agent's `reindex_document` tool and
    this CLI share one implementation - two copies of "how a document is
    indexed" would drift, and the drift would be invisible until a search
    returned something stale.

    Wholesale replacement, like the CLI: the document's rows are deleted and
    re-inserted, so a section removed from the file disappears from the index
    rather than lingering as an orphan. Never prunes - see `main()`.

    The exclusion is enforced *here*, on the one function that writes, rather
    than by each caller. Anything that reaches the index goes through this
    line, so there is no second path to forget about.
    """
    path = check_indexable(path)
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

    if args.paths:
        # Checked up front and as a batch: a refusal halfway through an
        # explicit list would leave the index half-updated with no obvious
        # way to tell which half. `index_document` checks again - this is for
        # the message, that is for the guarantee.
        paths, refused = [], []
        for given in args.paths:
            try:
                paths.append(check_indexable(Path(given)))
            except NotIndexable as exc:
                refused.append(str(exc))
        if refused:
            for reason in refused:
                print(f"refused: {reason}", file=sys.stderr)
            print(f"\nrefused {len(refused)} of {len(args.paths)} paths; "
                  f"nothing was indexed", file=sys.stderr)
            return 1
    else:
        paths = documents()

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
        #
        # "Gone" covers two cases now: deleted from disk, and still on disk but
        # no longer in the corpus because it was excluded. Both leave rows
        # behind that a search could still return, so both are pruned.
        if conn and not args.paths:
            keep = [p.relative_to(REPO).as_posix() for p in paths]
            removed = store.prune(conn, keep)
    finally:
        if conn:
            conn.close()

    verb = "would index" if args.dry_run else "indexed"
    print(f"\n{verb} {total} chunks from {len(paths)} documents using {MODEL}")
    if removed:
        print(f"pruned {removed} chunks from documents no longer in the corpus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
