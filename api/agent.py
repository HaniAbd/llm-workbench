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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import index_docs
import prompts
import store
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

ACTION_REJECTED = "ACTION_REJECTED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
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
    "approval_expired",   # an action needed a person and nobody answered
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
    "approval_expired": (
        "Stopped: an action needed a person's approval and nobody decided in "
        "time. Nothing was changed."
    ),
}


# --- approval --------------------------------------------------------------
#
# The loop knows only that some tools must be asked about before they run, and
# how to act on the three answers. *Who* is asked, and how the waiting is done,
# is the caller's problem - `runs.py` implements it with a background thread
# and an HTTP endpoint. This module stays free of both, exactly as `tracing.py`
# stays free of OpenAI and FastAPI.

ApprovalVerdict = Literal["approved", "rejected", "expired"]


@dataclass(frozen=True)
class ApprovalRequest:
    """What the loop intends to do, for a person to decide about.

    `effect` is the part that matters to a human and that the model does not
    get to write: it is declared on the tool, so it describes what the code
    will actually do rather than what the model says it will do.
    """

    tool: str
    arguments: dict[str, Any]
    description: str
    effect: str | None


@dataclass(frozen=True)
class Decision:
    verdict: ApprovalVerdict
    reason: str | None = None


def deny_all(request: ApprovalRequest) -> Decision:
    """The default approver: refuse everything.

    A loop with nobody attached must not be able to act. Defaulting the other
    way would mean that forgetting to pass an approver - in a test, a script,
    a future endpoint - silently removes the gate, and that failure is exactly
    the one this whole step exists to prevent.
    """
    return Decision(
        "rejected",
        "no approver is attached to this run, so actions cannot be authorised",
    )


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


def _indexable() -> dict[str, "Path"]:
    """Repository-relative path -> file, for every document indexing accepts.

    Read from `index_docs` rather than listed here, so the tool can never
    operate on something the indexer would not.
    """
    return {p.relative_to(index_docs.REPO).as_posix(): p
            for p in index_docs.documents()}


class ReindexArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="Repository-relative path of a markdown document that is "
                    "already part of the documentation, e.g. 'api/README.md'."
    )

    @field_validator("path")
    @classmethod
    def _must_be_an_indexable_document(cls, value: str) -> str:
        """Resolve against the indexer's own file list, not the filesystem.

        Doing this in the argument model rather than inside the tool has a
        point beyond tidiness: arguments are validated *before* the approval
        gate, so a person is never asked to approve re-indexing a path that
        does not exist, and a traversal attempt is a BAD_ARGUMENTS reply rather
        than something anyone has to think about.
        """
        allowed = _indexable()
        cleaned = value.strip().removeprefix("./")
        if cleaned not in allowed:
            raise ValueError(
                f"{value!r} is not an indexable document; choose one of: "
                + ", ".join(sorted(allowed))
            )
        return cleaned


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

    # Whether running this needs a person's say-so. Declared here, on the
    # server's own tool table, and read only from here. It is deliberately
    # *not* an argument the model fills in and not a field of the schema it is
    # shown: a model that can mark its own action safe has not been gated. The
    # model is told which tools are gated, but telling is all it can do.
    requires_approval: bool = False

    # What running it changes, in a sentence, for whoever has to decide.
    effect: str | None = None


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


def _run_reindex(client, model: str, args: ReindexArgs, span: ChatSpan) -> ToolResult:
    """Re-index one document. The only capability here that changes anything.

    Chosen as the acting tool because this project can justify it: it is the
    one genuine write the codebase already performs, its effect is real (rows
    in Postgres are deleted and re-inserted), it is reversible by running it
    again, and it needs no external service and no credentials. It also makes
    a sequence worth having - search the docs, find them stale, re-index, search
    again - which a read-only tool set cannot express.
    """
    path = _indexable()[args.path]          # validated by ReindexArgs
    try:
        with store.connect() as conn:
            store.ensure_schema(conn)
            before = store.stats(conn)["chunks"]
            written = index_docs.index_document(conn, path)
            after = store.stats(conn)["chunks"]
    except OperationalError:
        return ToolResult(False, INDEX_UNAVAILABLE,
                          f"{INDEX_UNAVAILABLE}: the document index is not reachable, "
                          "so nothing was re-indexed. Do not retry this tool.")
    except OSError as exc:
        return ToolResult(False, TOOL_FAILED,
                          f"{TOOL_FAILED}: reindex_document could not read "
                          f"{args.path}: {type(exc).__name__}.")

    return ToolResult(
        True, None,
        f"Re-indexed {args.path}: {written} chunks written. "
        f"The index now holds {after} chunks (was {before}).",
        {"path": args.path, "chunks_written": written,
         "index_chunks_before": before, "index_chunks_after": after},
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
        Tool(
            name="reindex_document",
            description=(
                "Re-index one markdown document so that searches reflect the "
                "file as it is on disk now. Use when a document has been "
                "edited, or when search results look out of date. "
                "This tool changes stored data and requires a person's "
                "approval before it runs."
            ),
            arguments=ReindexArgs,
            run=_run_reindex,
            requires_approval=True,
            effect=(
                "Deletes this document's rows from the search index and "
                "re-inserts them from the file on disk. Reversible by running "
                "it again. Nothing outside the index is touched."
            ),
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


def _resolve(name: str, raw: str) -> tuple[Tool | None, BaseModel | None, ToolResult | None]:
    """Find the tool and validate its arguments. Never raises.

    Split out of execution so the approval gate has somewhere to sit: the
    arguments a person is shown are the validated ones, and an unknown tool or
    nonsense arguments are refused without anybody being asked about them.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return None, None, ToolResult(
            False, NO_SUCH_TOOL,
            f"{NO_SUCH_TOOL}: there is no tool called {name!r}. "
            f"The only tools that exist are: {', '.join(TOOLS)}.",
        )

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
        return tool, None, ToolResult(
            False, BAD_ARGUMENTS,
            f"{BAD_ARGUMENTS}: {name} was called with arguments that are not valid "
            f"({detail}). Every value must be a plain string, not an object "
            f"describing one. Call it again with exactly this shape: {example}",
        )

    return tool, args, None


def run_agent(client, model: str, question: str, span: ChatSpan | None = None,
              approve: Callable[[ApprovalRequest], Decision] = deny_all
              ) -> AgentRun:
    """Let the model choose capabilities until it answers or runs out of room.

    `approve` is called before any tool marked `requires_approval` runs, and may
    block for as long as it likes - waiting for a person is not the loop's
    business. It defaults to refusing, so a caller that forgets to attach one
    gets a gated agent rather than an ungated one.
    """
    prompt = prompts.get(
        "agent_loop",
        not_in_docs=NOT_IN_DOCS, no_such_tool=NO_SUCH_TOOL,
        bad_arguments=BAD_ARGUMENTS, tool_failed=TOOL_FAILED,
        index_unavailable=INDEX_UNAVAILABLE, action_rejected=ACTION_REJECTED,
        max_steps=MAX_STEPS,
    )
    schemas = tool_schemas()
    messages: list[dict] = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": question},
    ]

    steps: list[AgentStep] = []
    repeats: dict[tuple[str, str], int] = {}
    # Actions a person has already refused in this run, so the model cannot
    # put the same question to them twice by asking again.
    refused: dict[tuple[str, str], str] = {}
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

            tool, args, failure = _resolve(name, raw)
            parsed = None if args is None else args.model_dump()

            if failure is not None:
                result = failure
            elif not tool.requires_approval:
                result = tool.run(client, model, args, span)
            else:
                # A person decides. Keyed on the validated arguments, so
                # asking for the same action in differently-spelled JSON is
                # still recognised as the same action.
                key = (name, json.dumps(parsed, sort_keys=True))
                if key in refused:
                    # Already refused once. Re-asking a person the identical
                    # question is not something the model gets to do.
                    result = ToolResult(
                        False, ACTION_REJECTED,
                        f"{ACTION_REJECTED}: this exact action was already refused "
                        f"({refused[key]}). It was not put to anyone again. Do "
                        f"something else or answer without it.")
                    if span is not None:
                        span.record("approval_skipped", tool=name, arguments=parsed,
                                    reason="already refused in this run")
                else:
                    request = ApprovalRequest(tool=name, arguments=parsed,
                                              description=tool.description,
                                              effect=tool.effect)
                    if span is not None:
                        span.record("approval_requested", tool=name,
                                    arguments=parsed, effect=tool.effect)
                    waited_from = time.perf_counter()
                    decision = approve(request)
                    waited = time.perf_counter() - waited_from
                    # Waiting for a human is not the agent working, so it does
                    # not spend the time budget. Without this, any approval
                    # slower than the budget would fail the run on arrival.
                    deadline += waited

                    if span is not None:
                        span.record("approval_decided", tool=name,
                                    verdict=decision.verdict, reason=decision.reason,
                                    waited_ms=round(waited * 1000))

                    if decision.verdict == "expired":
                        # Terminal, but still recorded as a step. A run that
                        # ends with nothing in `steps` reads as a run that did
                        # nothing, when in fact it asked and was never answered.
                        expired = ToolResult(
                            False, APPROVAL_EXPIRED,
                            f"{APPROVAL_EXPIRED}: {decision.reason}. The action "
                            f"did not run and nothing was changed.")
                        steps.append(AgentStep(
                            n=len(steps) + 1, tool=name, arguments=parsed,
                            raw_arguments=raw, ok=False,
                            signal=APPROVAL_EXPIRED, result=expired.content))
                        if span is not None:
                            span.record("tool_call", n=len(steps), tool=name,
                                        arguments=parsed, raw_arguments=raw,
                                        ok=False, signal=APPROVAL_EXPIRED,
                                        result_chars=len(expired.content))
                        return finish("approval_expired",
                                      _BOUND_MESSAGES["approval_expired"])
                    if decision.verdict == "rejected":
                        reason = decision.reason or "no reason given"
                        refused[key] = reason
                        result = ToolResult(
                            False, ACTION_REJECTED,
                            f"{ACTION_REJECTED}: a person refused this action "
                            f"({reason}). It did not run and nothing was changed. "
                            f"Do not request it again; continue without it or "
                            f"explain that you could not proceed.")
                    else:
                        result = tool.run(client, model, args, span)

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
