"""Serving the source document behind a retrieved passage.

The index stores chunks, not documents, and a chunk is not a document: the
heading lines are consumed into the heading path and never stored, so
reassembling `api/README.md` from its 32 chunks yields 29,140 characters
against the file's 44,558. A third of it is missing. What is served here is
therefore the file itself, read from disk at request time.

The consequence is that a file can have changed since it was indexed, and
already has: measured against the current tree, 31 of 32 chunks of
`api/README.md` still appear verbatim, 12 of 14 of `CLAUDE.md`, and 0 of 1 of
`web/AGENTS.md`. A passage that no longer appears in its document is an
ordinary outcome here, reported rather than raised.

Only documents the index knows are served. That is both the meaning of the
endpoint - these are the sources retrieval drew on - and what makes a path
traversal impossible: the path has to already be a row in the index.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

REPO = Path(__file__).resolve().parent.parent


class Document(BaseModel):
    """A source document, as it is on disk now."""

    path: str
    # None when the document is indexed but no longer on disk. The caller is
    # expected to say so rather than to treat it as an empty file.
    text: str | None
    on_disk: bool
    indexed_at: datetime | None
    chunk_count: int


def load_document(conn, path: str) -> Document | None:
    """The document at `path`, or None if the index has never seen it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), max(indexed_at) FROM doc_chunks WHERE source = %s",
            (path,),
        )
        chunk_count, indexed_at = cur.fetchone()

    if not chunk_count:
        return None

    # The path came from the index, so it cannot escape the repository; this
    # re-checks it anyway rather than trusting that invariant from here.
    resolved = (REPO / path).resolve()
    if not resolved.is_relative_to(REPO) or not resolved.is_file():
        return Document(path=path, text=None, on_disk=False,
                        indexed_at=indexed_at, chunk_count=chunk_count)

    return Document(
        path=path,
        text=resolved.read_text(encoding="utf-8"),
        on_disk=True,
        indexed_at=indexed_at,
        chunk_count=chunk_count,
    )
