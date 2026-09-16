"""The document index: chunks and their vectors in Postgres with pgvector.

Schema is created on demand so there is no migration step to forget. One table:
a chunk knows which document it came from, where in that document, and what it
says. `source` plus `ordinal` is the natural key - re-indexing a document
replaces its rows rather than accumulating duplicates.

Similarity is cosine distance (`<=>`), which pgvector returns as 0 for
identical and 2 for opposite. The score reported upward is `1 - distance`, so
bigger is better and it reads like a similarity.
"""

import os

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from embeddings import DIM

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://workbench:workbench@localhost:5433/workbench"
)


def connect():
    conn = psycopg.connect(DATABASE_URL)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def ensure_schema(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS doc_chunks (
            id           bigserial PRIMARY KEY,
            source       text        NOT NULL,
            heading_path text        NOT NULL,
            ordinal      int         NOT NULL,
            text         text        NOT NULL,
            embedding    vector({DIM}) NOT NULL,
            model        text        NOT NULL,
            indexed_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (source, ordinal)
        )
        """
    )
    conn.commit()


def replace_document(conn, source: str, rows: list[dict], model: str) -> int:
    """Re-index one document: drop its old rows, insert the new ones.

    Delete-then-insert rather than upsert because a document that loses a
    section should lose the corresponding rows, and an upsert keyed on
    (source, ordinal) would leave the tail behind.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM doc_chunks WHERE source = %s", (source,))
        for row in rows:
            cur.execute(
                """
                INSERT INTO doc_chunks
                    (source, heading_path, ordinal, text, embedding, model)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (source, row["heading_path"], row["ordinal"], row["text"],
                 row["embedding"], model),
            )
    conn.commit()
    return len(rows)


def prune(conn, keep_sources: list[str]) -> int:
    """Drop chunks whose document is no longer part of the corpus.

    Two ways to leave it: deleted from disk, or excluded from indexing while
    still sitting there. Either way the rows would otherwise stay searchable.

    Only meaningful after a full run: re-indexing one document says nothing
    about whether the others still belong.
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM doc_chunks WHERE source <> ALL(%s)", (keep_sources,))
        removed = cur.rowcount
    conn.commit()
    return removed


def _diversify(rows: list[dict], limit: int, reserve: int) -> list[dict]:
    """Top-(limit - reserve) by similarity, then `reserve` slots for documents
    not already represented.

    A flat per-source cap was tried and cost far more than it gained: a direct
    question often needs the third chunk of the document that dominates the
    ranking, and a cap evicts it. Reserving only the final slot leaves the
    head of the ranking untouched and still guarantees a second document is
    seen by a question whose answer spans two.

    Falls back to plain similarity order if there is no unrepresented document
    to promote, so this never returns fewer rows than a plain top-k.
    """
    head = rows[: max(0, limit - reserve)]
    seen = {r["source"] for r in head}
    tail = []
    for r in rows[len(head):]:
        if len(tail) == reserve:
            break
        if r["source"] not in seen:
            tail.append(r)
            seen.add(r["source"])
    if len(head) + len(tail) < limit:
        rest = [r for r in rows if r not in head and r not in tail]
        tail += rest[: limit - len(head) - len(tail)]
    return head + tail


def search(conn, query_vector, limit: int, pool: int | None = None,
           reserve: int | None = None) -> list[dict]:
    """The naive retrieval: nearest neighbours by cosine distance, nothing else.

    No keyword matching, no reranking, no filtering by score. A fixed number of
    rows comes back whether or not any of them is relevant, which is exactly
    the behaviour worth seeing before adding anything.
    """
    # Wrapped in Vector(): a bare list adapts to double precision[], which has
    # no <=> operator against a vector column.
    vec = Vector(query_vector)
    fetch = pool if reserve else limit
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, heading_path, text, 1 - (embedding <=> %s) AS score
            FROM doc_chunks
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (vec, vec, fetch),
        )
        rows = [
            {"source": s, "heading_path": h, "text": t, "score": round(float(sc), 4)}
            for s, h, t, sc in cur.fetchall()
        ]
    if reserve:
        return _diversify(rows, limit, reserve)
    return rows


def stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(DISTINCT source), max(indexed_at),"
            " coalesce(string_agg(DISTINCT model, ','), '') FROM doc_chunks"
        )
        chunks, sources, last, models = cur.fetchone()
    return {"chunks": chunks, "documents": sources, "indexed_at": last, "models": models}
