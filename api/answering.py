"""Answering a question from the repository's own documentation.

Deliberately the naive version: embed the question, take the nearest K
passages by cosine similarity, put them in the prompt. No keyword search, no
reranking, no threshold - a fixed number of passages comes back whether or not
any of them is relevant, and the answer is only as good as that.

Not streamed. The sources are as much of the result as the prose, and they are
only known once retrieval has finished; returning them alongside a complete
answer keeps the two from arriving separately.
"""

from pydantic import BaseModel, ConfigDict, ValidationError

import sys

import embeddings
from openai import OpenAIError

import prompts
import store
from embeddings import embed_one

# Fixed, by design. Tuning this, or dropping passages below a score, is the
# kind of thing worth doing only after seeing what the fixed version gets wrong.
RETRIEVE_K = 4

# Pick the final K from a larger candidate pool: the first K-RESERVE slots by
# pure similarity, the remainder for documents not already represented. K is
# deliberately unchanged, so the model sees the same amount of context and any
# change in the answer score comes from which passages arrived, not how many.
CANDIDATE_POOL = 20
RESERVE_FOR_OTHER_SOURCES = 1

# A backstop, not the mechanism. The prompt already declines 9 of 10
# unanswerable questions unaided; this catches the one it misses, where the
# corpus happens to contain a passage *about* the question.
#
# The margin is thin and should be read as such: on the eval set the lowest
# ANSWERABLE question scores 0.5282 and the highest question this catches
# scores 0.5065. Twenty-two thousandths, measured on ten off-topic questions.
SIMILARITY_FLOOR = 0.52

REFUSAL_SENTINEL = "NOT_IN_DOCS"

# Whether the reply declined is decided by a second, schema-constrained call
# rather than by shaping the answer prompt. Two earlier attempts shaped the
# prompt instead and both cost answer quality: constraining the whole reply to
# JSON took `answer` 0.823 -> 0.618, and a sentinel token took `direct`
# 0.938 -> 0.750. This leaves the answer prompt untouched, at the cost of a
# second call per question.
#
# A server-side prose check was rejected for a different reason: the eval
# scores refusal from prose markers too, so the suite would be checking the
# server's heuristic against a copy of itself rather than against the model.


def retrieval_config() -> dict:
    """Every knob that shapes a retrieval result, collected rather than listed.

    Introspection, not a hand-written list, because a hand-written list is the
    failure this exists to prevent: a knob added and not added here would leave
    a run record that *looks* like it captured the configuration while quietly
    omitting the thing that changed.

    The rule is "module-level constant" - any new UPPER_CASE scalar in this
    module or in `embeddings` is picked up with no further work. It errs
    towards including too much: a constant that turns out not to affect
    retrieval causes a comparison to be flagged when nothing relevant moved,
    which is noisy but safe. The reverse is not.
    """
    def constants(module, prefix):
        return {
            f"{prefix}.{name}": value
            for name, value in vars(module).items()
            if name.isupper()
            and not name.startswith("_")
            and isinstance(value, (int, float, str, bool))
        }

    return {**constants(sys.modules[__name__], "answering"),
            **constants(embeddings, "embeddings")}


class Source(BaseModel):
    """Where a passage came from, precisely enough to go and check."""

    source: str
    heading_path: str
    score: float


class Answer(BaseModel):
    # False means the documentation does not contain the answer. That is a
    # correct outcome, not an error: the request succeeded and retrieval ran.
    # Failures are 502/503 and carry no answer at all.
    answered: bool
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
        return store.search(conn, vector, k, pool=CANDIDATE_POOL,
                            reserve=RESERVE_FOR_OTHER_SOURCES)


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

    best = rows[0]["score"] if rows else 0.0
    if best < SIMILARITY_FLOOR:
        if span is not None:
            span.record("refused_below_floor", best=best, floor=SIMILARITY_FLOOR)
            span.raw_output = ""
        return Answer(
            answered=False,
            answer="I could not find anything in the documentation that answers this.",
            sources=[Source(**{k: v for k, v in row.items() if k != "text"})
                     for row in rows],
            prompt_id="similarity-floor",
        )

    prompt = prompts.get("answer_question", sentinel=REFUSAL_SENTINEL,
                         passages=_format_passages(rows))
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

    # The model marks a refusal with a sentinel at the very start. Shaping the
    # prompt this way costs some answer quality (direct 0.938 -> 0.750) but is
    # the only mechanism measured that produces a trustworthy flag: a separate
    # schema-constrained judge call scored 22/31 and quintupled latency, and
    # constraining the whole reply to JSON destroyed the prose.
    answered = not text.lstrip().startswith(REFUSAL_SENTINEL)
    if not answered:
        text = text.lstrip()[len(REFUSAL_SENTINEL):].lstrip(" :.\n") or (
            "I could not find anything in the documentation that answers this.")
    if span is not None:
        span.record("model_verdict", answered=answered)

    return Answer(
        answered=answered,
        answer=text,
        sources=[Source(**r) for r in
                 ({k: v for k, v in row.items() if k != "text"} for row in rows)],
        prompt_id=prompt.id,
    )
