"""Ticket classification: one schema-constrained, non-streaming LLM call.

Correctness here does not come from prompting. `llama3.2` is small and will
happily return prose, invented fields, or a category that is not in the list.
It comes from `response_format={"type": "json_schema", ...}`, which Ollama
enforces as a decoding grammar — tokens outside the schema cannot be produced
at all. Measured: plain JSON mode returned `"category": "Billing Issue"` with
invented sibling fields; the same call under a schema returned exact values,
and kept doing so under prompt-injection attempts.

The grammar guarantees *shape*, never *meaning*: an empty string still yields a
confident-looking classification. Meaning is handled in two other places — the
route rejects blank input before spending a call, and `is_support_ticket` lets
the model disown text that is not a ticket.

One pydantic model is both the schema sent to the provider and the validator
for what comes back, so the two cannot drift apart.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

import prompts

CATEGORIES = ("billing", "technical", "account", "feedback", "other")
URGENCIES = ("low", "medium", "high")
SENTIMENTS = ("positive", "neutral", "frustrated", "angry")


class Classification(BaseModel):
    """The response body, and the schema the model is constrained to.

    `extra="forbid"` becomes `additionalProperties: false` in the generated
    schema, which is what stops the model adding fields of its own.
    """

    model_config = ConfigDict(extra="forbid")

    is_support_ticket: bool
    category: Literal[CATEGORIES]  # type: ignore[valid-type]
    urgency: Literal[URGENCIES]  # type: ignore[valid-type]
    sentiment: Literal[SENTIMENTS]  # type: ignore[valid-type]
    requires_human: bool


class ClassificationResult(Classification):
    """What the caller receives: the classification plus its provenance.

    Deliberately *not* the schema handed to the model — `Classification` is.
    Adding `prompt_id` there would ask the model to invent its own prompt id.
    """

    prompt_id: str


# Built once. Pydantic inlines Literal enums rather than emitting $defs/$ref,
# which matters because Ollama does not resolve references in a format schema.
_SCHEMA = Classification.model_json_schema()

# The single defined answer for anything the model says is not a ticket.
# The model's own category for such text is meaningless and actively
# misleading — off-topic prose came back as "billing" in testing — so it is
# replaced rather than passed through.
NOT_A_TICKET = Classification(
    is_support_ticket=False,
    category="other",
    urgency="low",
    sentiment="neutral",
    requires_human=False,
)

def _prompt() -> prompts.Prompt:
    """The classification prompt, rendered with the enums it must agree with.

    The allowed values are injected rather than written into the file so the
    prompt and the JSON schema cannot disagree about what is classifiable.
    """
    return prompts.get(
        "classify_ticket",
        categories=" | ".join(CATEGORIES),
        urgencies=" | ".join(URGENCIES),
        sentiments=" | ".join(SENTIMENTS),
    )


class ClassificationError(RuntimeError):
    """The model returned something that is not a valid classification.

    Raised rather than returned so a partial or malformed result can never be
    mistaken for a real one by a caller that forgets to check.
    """


def classify(client, model: str, text: str, span=None) -> ClassificationResult:
    """Classify one ticket. Returns a valid `Classification` or raises.

    `span`, when given, is a `tracing.ChatSpan` to record usage on. There is no
    time-to-first-token to mark: this call is not streamed, by design — the
    caller wants the whole object or nothing, and a half-parsed object is
    worthless.
    """
    prompt = _prompt()
    if span is not None:
        span.prompt_id = prompt.id

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt.text},
            {"role": "user", "content": text},
        ],
        temperature=0,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ticket_classification",
                "schema": _SCHEMA,
                "strict": True,
            },
        },
    )

    if span is not None and completion.usage is not None:
        span.set_usage(
            completion.usage.prompt_tokens, completion.usage.completion_tokens
        )
    choice = completion.choices[0]
    if span is not None:
        span.finish_reason = choice.finish_reason

    raw = choice.message.content or ""

    # Validates JSON syntax, field presence, enum membership and the absence of
    # extra keys in one step. Prose-wrapped JSON fails here at the parse stage.
    try:
        result = Classification.model_validate_json(raw)
    except ValidationError as exc:
        raise ClassificationError(
            f"model returned output that is not a valid classification: {raw[:200]!r}"
        ) from exc

    classification = NOT_A_TICKET if not result.is_support_ticket else result
    return ClassificationResult(**classification.model_dump(), prompt_id=prompt.id)
