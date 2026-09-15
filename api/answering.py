"""Answering a question from the repository's own documentation.

Deliberately the naive version: embed the question, take the nearest K
passages by cosine similarity, put them in the prompt. No keyword search, no
reranking, no threshold - a fixed number of passages comes back whether or not
any of them is relevant, and the answer is only as good as that.

Not streamed. The sources are as much of the result as the prose, and they are
only known once retrieval has finished; returning them alongside a complete
answer keeps the two from arriving separately.
"""

from pydantic import BaseModel, ValidationError  # noqa: F401  (kept for parity)

import prompts
import store
from embeddings import embed_one

# Fixed, by design. Tuning this, or dropping passages below a score, is the
# kind of thing worth doing only after seeing what the fixed version gets wrong.
RETRIEVE_K = 4


class Source(BaseModel):
    """Where a passage came from, precisely enough to go and check."""

    source: str
    heading_path: str
    score: float


class Answer(BaseModel):
    answer: str
    sources: list[Source]
    prompt_id: str


class AnsweringError(RuntimeError):
    """The index could not be read, or the model returned nothing."""


def _format_passages(rows: list[dict]) -> str:
    return "\n\n".join(
        f"--- {r['heading_path']}\n{r['text']}" for r in rows
    )


def retrieve(question: str, k: int = RETRIEVE_K) -> list[dict]:
    """Nearest passages to the question. Opens and closes its own connection so
    a re-index between requests is picked up with no restart."""
    vector = embed_one(question)
    with store.connect() as conn:
        return store.search(conn, vector, k)


def answer_question(client, model: str, question: str, span=None) -> Answer:
    rows = retrieve(question)

    if span is not None:
        span.retrieved = rows
        span.record(
            "retrieval",
            passages=len(rows),
            top_score=rows[0]["score"] if rows else None,
            k=RETRIEVE_K,
        )

    prompt = prompts.get("answer_question", passages=_format_passages(rows))
    messages = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": question},
    ]
    if span is not None:
        span.prompt_id = prompt.id
        span.messages_sent = messages
        span.record("request_sent", message_count=len(messages))

    completion = client.chat.completions.create(
        model=model, messages=messages, temperature=0
    )
    choice = completion.choices[0]
    text = (choice.message.content or "").strip()

    if span is not None:
        if completion.usage is not None:
            span.set_usage(
                completion.usage.prompt_tokens, completion.usage.completion_tokens
            )
        span.finish_reason = choice.finish_reason
        span.raw_output = text

    if not text:
        raise AnsweringError("the model returned an empty answer")

    return Answer(
        answer=text,
        sources=[Source(**r) for r in
                 ({k: v for k, v in row.items() if k != "text"} for row in rows)],
        prompt_id=prompt.id,
    )
