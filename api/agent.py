"""A hand-written agent loop over the capabilities this API already has.

The model is given the two things this service can do - answer from the
indexed documentation, and classify a support ticket - and decides for itself
which to use, in what order, and when it has enough to answer. There is no
orchestration framework here on purpose: a later step rebuilds this on one,
and the comparison only means something if this half is written out in full.

The loop is mostly error handling, because that is where the difficulty is.
Three things go wrong routinely with a small local model, and none of them is
allowed to raise:

  the tool does not exist      the model invents a name  -> NO_SUCH_TOOL
  the arguments make no sense  wrong type, extra keys    -> BAD_ARGUMENTS
  the capability fails or
  finds nothing                                          -> NOT_IN_DOCS,
                                                            TOOL_FAILED,
                                                            INDEX_UNAVAILABLE

Each becomes an ordinary tool result handed back to the model as text, so the
model can act on it. `NOT_IN_DOCS` is not invented here - it is
`answering.REFUSAL_SENTINEL`, the signal retrieval already publishes when it
finds nothing, reused rather than duplicated.

Termination is explicit. The loop is bounded three ways and every bound is a
reported `stop_reason`, never a silent stop and never an exception: a run that
hits one returns 200 with its steps intact and says which bound it hit.

What this does NOT do is repair the model's mistakes. Malformed arguments are
rejected and reported rather than coerced into something workable, even where
the intent is obvious - see the README for the measured failure modes. A loop
tuned until it looks good would say nothing about the model underneath it.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from openai import OpenAIError
from psycopg import OperationalError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import prompts
from answering import REFUSAL_SENTINEL, AnsweringError, answer_question
from classification import ClassificationError, classify
from tracing import ChatSpan, chat_span

# --- bounds ----------------------------------------------------------------
#
# Three, because they fail differently. MAX_STEPS bounds a model that keeps
# calling tools; TIME_BUDGET bounds one whose tools are individually slow
# (`search_docs` is a full retrieval-and-answer call, seconds each);
# MAX_REPEATED_CALLS bounds the classic small-model failure of asking for the
# identical thing forever, which neither of the others catches quickly.

MAX_STEPS = 6
TIME_BUDGET_S = 180.0
MAX_REPEATED_CALLS = 2

# --- machine-readable outcomes ---------------------------------------------
#
# Rendered into the prompt as well as returned to the model, so the text the
# model is told to watch for and the text it actually receives cannot drift.

NO_SUCH_TOOL = "NO_SUCH_TOOL"
BAD_ARGUMENTS = "BAD_ARGUMENTS"
TOOL_FAILED = "TOOL_FAILED"
INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
NOT_IN_DOCS = REFUSAL_SENTINEL


StopReason = Literal[
    "answered",           # the model replied with prose and asked for no tool
    "max_steps",          # bound: too many model turns
    "time_budget",        # bound: wall clock
    "repeated_tool_call", # bound: same tool, same arguments, over and over
    "empty_response",     # the model returned neither text nor a tool call
]

# What the caller is told when a bound ended the run. There is deliberately no
# extra "now summarise what you have" call: that would hide the bound behind an
# answer, and an answer assembled after running out of room is exactly the
# thing worth seeing as a failure.
_BOUND_MESSAGES: dict[str, str] = {
    "max_steps": (
        f"Stopped after {MAX_STEPS} steps without reaching an answer. "
        "The steps below show what was tried."
    ),
    "time_budget": (
        f"Stopped after exceeding the {TIME_BUDGET_S:.0f}s budget without "
        "reaching an answer. The steps below show what was tried."
    ),
    "repeated_tool_call": (
        "Stopped: the model asked for the same tool with the same arguments "
        "repeatedly and was making no progress."
    ),
    "empty_response": "Stopped: the model returned neither an answer nor a tool call.",
}


class AgentError(RuntimeError):
    """The loop itself could not run - the model call failed.

    Distinct from a *tool* failing, which is an ordinary result the model is
    expected to handle. Without a model there is no loop, so this is raised.
    """


# --- arguments -------------------------------------------------------------
#
# One pydantic model per tool is both the JSON Schema advertised to the model
# and the validator for what comes back, the same trick `classification.py`
# uses for the response. `extra="forbid"` becomes `additionalProperties:
# false`, which is what turns an invented extra key into a reported
# BAD_ARGUMENTS instead of a silently ignored one.


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(
        description="A complete natural-language question, as plain text."
    )


class ClassifyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="The raw text of one support ticket, as plain text.")


@dataclass(frozen=True)
class ToolResult:
    """What a capability returned, for the model and for the trace.

    `content` is the only part the model sees. It leads with `signal` when
    there is one, because a small model reads the first token of a tool result
    far more reliably than it reads a JSON field.
    """

    ok: bool
    signal: str | None
    content: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    run: Callable[..., ToolResult]


def _sources(answer) -> str:
    return "\n".join(f"- {s.heading_path} ({s.score:.3f})" for s in answer.sources)


def _run_search(client, model: str, args: SearchArgs, span: ChatSpan) -> ToolResult:
    """The /ask capability, unchanged, wrapped as a tool.

    Opens its own span, so this call gets its own `llm_call` log line exactly
    like a direct /ask request would, and its retrieval detail is not written
    over the agent's own conversation.
    """
    try:
        with chat_span(model) as sub:
            answer = answer_question(client, model, args.question, sub)
    except OperationalError:
        return ToolResult(False, INDEX_UNAVAILABLE,
                          f"{INDEX_UNAVAILABLE}: the document index is not reachable, "
                          "so nothing could be looked up. Do not retry this tool.")
    except (AnsweringError, OpenAIError) as exc:
        return ToolResult(False, TOOL_FAILED,
                          f"{TOOL_FAILED}: search_docs raised {type(exc).__name__}.")

    # Everything retrieval considered during the run, accumulated rather than
    # overwritten: a run that searches three times retrieved all of it.
    if sub.retrieved:
        span.retrieved = (span.retrieved or []) + sub.retrieved

    detail = {
        "passages": len(answer.sources),
        "top_score": answer.sources[0].score if answer.sources else None,
        "inner_input_tokens": sub.input_tokens,
        "inner_output_tokens": sub.output_tokens,
        "inner_latency_ms": sub.latency_ms,
        "inner_prompt_id": answer.prompt_id,
    }

    # Retrieval's existing "found nothing" signal, reused. This is the one the
    # model handles best of all of them (measured - see the README).
    if not answer.answered:
        return ToolResult(
            False, NOT_IN_DOCS,
            f"{NOT_IN_DOCS}: the documentation does not answer that. "
            "Do not answer it from your own knowledge; say it is not documented.",
            detail,
        )

    return ToolResult(True, None, f"{answer.answer}\n\nPassages used:\n{_sources(answer)}",
                      detail)


def _run_classify(client, model: str, args: ClassifyArgs, span: ChatSpan) -> ToolResult:
    """The /classify capability, unchanged, wrapped as a tool."""
    try:
        with chat_span(model) as sub:
            result = classify(client, model, args.text, sub)
    except (ClassificationError, OpenAIError) as exc:
        return ToolResult(False, TOOL_FAILED,
                          f"{TOOL_FAILED}: classify_ticket raised {type(exc).__name__}.")

    payload = result.model_dump()
    return ToolResult(
        True, None, json.dumps(payload),
        {"classification": payload,
         "inner_input_tokens": sub.input_tokens,
         "inner_output_tokens": sub.output_tokens,
         "inner_latency_ms": sub.latency_ms},
    )


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            name="search_docs",
            description=(
                "Search this repository's own markdown documentation and answer "
                "from it. Use for any question about how this project works. "
                f"Returns {NOT_IN_DOCS} when the documentation does not cover it."
            ),
            arguments=SearchArgs,
            run=_run_search,
        ),
        Tool(
            name="classify_ticket",
            description=(
                "Classify one customer support ticket. Returns JSON with "
                "category, urgency, sentiment and whether a human is required. "
                "Use only on the text of an actual ticket."
            ),
            arguments=ClassifyArgs,
            run=_run_classify,
        ),
    )
}


def tool_schemas() -> list[dict]:
    """The `tools` payload, derived from the argument models."""
    return [
        {"type": "function",
         "function": {"name": t.name,
                      "description": t.description,
                      "parameters": t.arguments.model_json_schema()}}
        for t in TOOLS.values()
    ]


# --- the result ------------------------------------------------------------


class AgentStep(BaseModel):
    """One tool the model asked for, and what came back."""

    n: int
    tool: str
    # Parsed and validated arguments, or None when they were neither. The raw
    # string is kept either way: with a small model the exact malformed text is
    # the interesting part, and a parsed view would hide it.
    arguments: dict | None
    raw_arguments: str
    ok: bool
    signal: str | None
    result: str


class AgentRun(BaseModel):
    """What the loop did, and why it stopped."""

    answer: str
    # The single source of truth for how the run ended. A bound is reported
    # here with a 200, not raised: the steps are still worth having.
    stop_reason: StopReason  # type: ignore[valid-type]
    steps: list[AgentStep]
    model_calls: int
    tools_available: list[str]
    prompt_id: str


def _for_trace(messages: list[dict]) -> list[dict]:
    """Project the working conversation onto the trace's role/content shape.

    An assistant turn that requested tools carries them in `tool_calls`, not in
    `content`, and `tracing.SentMessage` has no field for that. Rendering them
    into the text keeps them visible in the panel without widening a model
    shared with /ask and /classify. The verbatim arguments are on the steps.
    """
    out = []
    for m in messages:
        content = m.get("content") or ""
        if m.get("tool_calls"):
            rendered = ", ".join(
                f"{c['function']['name']}({c['function']['arguments']})"
                for c in m["tool_calls"]
            )
            content = f"{content}[requested tools: {rendered}]".strip()
        out.append({"role": m["role"], "content": content})
    return out


def _invoke(client, model: str, name: str, raw: str, span: ChatSpan
            ) -> tuple[ToolResult, dict | None]:
    """Run one requested tool. Never raises - every failure is a ToolResult."""
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(
            False, NO_SUCH_TOOL,
            f"{NO_SUCH_TOOL}: there is no tool called {name!r}. "
            f"The only tools that exist are: {', '.join(TOOLS)}.",
        ), None

    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            raise ValueError("arguments must be a JSON object")
        args = tool.arguments.model_validate(data)
    except (json.JSONDecodeError, ValueError, ValidationError) as exc:
        detail = str(exc).splitlines()[0][:200]
        example = json.dumps(
            {n: f"<the {n}>" for n in tool.arguments.model_fields}
        )
        return ToolResult(
            False, BAD_ARGUMENTS,
            f"{BAD_ARGUMENTS}: {name} was called with arguments that are not valid "
            f"({detail}). Every value must be a plain string, not an object "
            f"describing one. Call it again with exactly this shape: {example}",
        ), None

    return tool.run(client, model, args, span), args.model_dump()


def run_agent(client, model: str, question: str, span: ChatSpan | None = None
              ) -> AgentRun:
    """Let the model choose capabilities until it answers or runs out of room."""
    prompt = prompts.get(
        "agent_loop",
        not_in_docs=NOT_IN_DOCS, no_such_tool=NO_SUCH_TOOL,
        bad_arguments=BAD_ARGUMENTS, tool_failed=TOOL_FAILED,
        index_unavailable=INDEX_UNAVAILABLE, max_steps=MAX_STEPS,
    )
    schemas = tool_schemas()
    messages: list[dict] = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": question},
    ]

    steps: list[AgentStep] = []
    repeats: dict[tuple[str, str], int] = {}
    tokens_in = tokens_out = 0
    model_calls = 0
    deadline = time.perf_counter() + TIME_BUDGET_S

    if span is not None:
        span.prompt_id = prompt.id
        span.record("agent_start", tools=list(TOOLS), max_steps=MAX_STEPS,
                    time_budget_s=TIME_BUDGET_S)

    def finish(reason: StopReason, answer: str) -> AgentRun:
        if span is not None:
            span.messages_sent = _for_trace(messages)
            span.raw_output = answer
            span.record("agent_stop", stop_reason=reason, steps=len(steps),
                        model_calls=model_calls)
        return AgentRun(answer=answer, stop_reason=reason, steps=steps,
                        model_calls=model_calls, tools_available=list(TOOLS),
                        prompt_id=prompt.id)

    for turn in range(1, MAX_STEPS + 1):
        if time.perf_counter() > deadline:
            return finish("time_budget", _BOUND_MESSAGES["time_budget"])

        try:
            completion = client.chat.completions.create(
                model=model, messages=messages, temperature=0, tools=schemas
            )
        except OpenAIError as exc:
            raise AgentError(f"agent model call failed: {type(exc).__name__}") from exc

        model_calls += 1
        if completion.usage is not None:
            tokens_in += completion.usage.prompt_tokens
            tokens_out += completion.usage.completion_tokens
            if span is not None:
                # Running totals for the whole run, the agent's own turns and
                # its tools' inner calls alike. Per-call figures stay on the
                # individual steps and on each inner span's own log line.
                span.set_usage(tokens_in, tokens_out)

        choice = completion.choices[0]
        message = choice.message
        calls = message.tool_calls or []
        text = (message.content or "").strip()

        if span is not None:
            span.finish_reason = choice.finish_reason
            span.record("model_turn", turn=turn, finish_reason=choice.finish_reason,
                        tool_calls=len(calls), content_chars=len(text))

        if not calls:
            # No tool requested: whatever it said is the answer. An empty reply
            # is a dead end rather than something to retry - retrying it would
            # be tuning, and the emptiness is the finding.
            if not text:
                return finish("empty_response", _BOUND_MESSAGES["empty_response"])
            return finish("answered", text)

        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name,
                              "arguments": c.function.arguments}}
                for c in calls
            ],
        })

        for call in calls:
            name = call.function.name
            raw = call.function.arguments or ""

            signature = (name, raw)
            repeats[signature] = repeats.get(signature, 0) + 1
            if repeats[signature] > MAX_REPEATED_CALLS:
                if span is not None:
                    span.record("bound_hit", bound="repeated_tool_call", tool=name,
                                times=repeats[signature])
                return finish("repeated_tool_call",
                              _BOUND_MESSAGES["repeated_tool_call"])

            result, parsed = _invoke(client, model, name, raw, span)
            step = AgentStep(n=len(steps) + 1, tool=name, arguments=parsed,
                             raw_arguments=raw, ok=result.ok, signal=result.signal,
                             result=result.content)
            steps.append(step)

            if span is not None:
                span.record("tool_call", n=step.n, tool=name, arguments=parsed,
                            raw_arguments=raw, ok=result.ok, signal=result.signal,
                            result_chars=len(result.content), **result.detail)

            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": result.content})

    if span is not None:
        span.record("bound_hit", bound="max_steps", steps=len(steps))
    return finish("max_steps", _BOUND_MESSAGES["max_steps"])
