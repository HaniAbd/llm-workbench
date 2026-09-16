import json
import os
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI, OpenAIError
from psycopg import OperationalError
from pydantic import BaseModel, Field, StringConstraints, model_validator

import prompts
import store
import runs
from agent import AgentRun
from answering import Answer, AnsweringError, answer_question, retrieval_config
from documents import Document, load_document
from classification import ClassificationError, ClassificationResult, classify
from tracing import TraceDocument, chat_span

load_dotenv(dotenv_path="../.env")

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)
MODEL = os.environ["MODEL"]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    # Any localhost port, not just 3000. The Next dev server falls back to
    # 3001, 3002, ... when its usual port is already taken by another
    # project, and a hardcoded origin silently breaks the page when it does.
    # Starlette fullmatches this, so it cannot match http://localhost.evil.com.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    # "system" is the server's to set. Constraining the role here means a
    # request carrying one is rejected by validation with a 422 before the
    # handler runs, rather than being silently dropped from the history.
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    # At least one message. An empty list would send the system prompt alone
    # and bill tokens for a conversation with no user turn, which is the same
    # hole /classify closes by rejecting blank text.
    messages: Annotated[list[Message], Field(min_length=1)]

    @model_validator(mode="after")
    def _must_carry_something_to_answer(self):
        """At least one message with actual content.

        Checked across the conversation rather than per message: an assistant
        turn can legitimately be empty (a stream that produced no tokens), and
        rejecting those would break a history the chat page had already built.
        """
        if not any(m.content.strip() for m in self.messages):
            raise ValueError("conversation has no non-empty message")
        return self


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest):
    prompt = prompts.get("chat_system")
    messages = [{"role": "system", "content": prompt.text}]
    messages += [m.model_dump() for m in req.messages]

    def generate():
        # The span opens before the request so latency covers the whole call,
        # and closes after the last yield so the log line is written once the
        # stream completes. Marking it costs nothing per token.
        with chat_span(MODEL) as span:
            span.prompt_id = prompt.id
            span.messages_sent = messages
            span.record("request_sent", message_count=len(messages))
            # The identity reaches the caller, not just the log. Additive: the
            # event dispatch in web/ is a bare if/else-if chain, so a client
            # that does not know this event ignores it.
            yield sse("prompt", {"id": prompt.id})

            stream = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=0,
                stream=True,
                stream_options={"include_usage": True},
            )

            deltas: list[str] = []
            for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        span.first_token()
                        deltas.append(choice.delta.content)
                        yield sse("token", {"text": choice.delta.content})
                    if choice.finish_reason:
                        span.finish_reason = choice.finish_reason
                        yield sse("finish", {"reason": choice.finish_reason})

                if chunk.usage:
                    span.set_usage(
                        chunk.usage.prompt_tokens, chunk.usage.completion_tokens
                    )
                    yield sse(
                        "usage",
                        {
                            "input_tokens": chunk.usage.prompt_tokens,
                            "output_tokens": chunk.usage.completion_tokens,
                        },
                    )

            # After every token, so it cannot delay the stream. The web
            # client ignores event types it does not know, so this is additive.
            span.raw_output = "".join(deltas)
            span.record("stream_complete", token_events=len(deltas))
            yield sse("trace", span.trace())

            yield sse("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- ticket classification -------------------------------------------------


class ClassifyRequest(BaseModel):
    # Blank input is a validation error, not a classification: it is decided
    # here rather than by the model, and costs no call. strip_whitespace makes
    # "   " and "\n\t" fail the same way "" does.
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ClassificationResponse(ClassificationResult):
    """The classification plus the development trace behind it."""

    # Typed, not `dict`, so the shape reaches OpenAPI and from there the
    # front end's generated types. This is the only hole that stopped the
    # published schema describing the whole response.
    trace: TraceDocument


def _failure(span, message: str) -> dict[str, Any]:
    """Error body that still shows what was sent and how far it got."""
    return {"message": message, "trace": span.trace() if span is not None else None}


@app.post("/classify", response_model=ClassificationResponse)
def classify_ticket(req: ClassifyRequest):
    """Classify one support ticket. Whole object or an error — never partial.

    Not streamed: the caller wants a complete result, and a half-received
    object cannot be validated against the schema.
    """
    captured = None
    try:
        with chat_span(MODEL) as span:
            captured = span
            result = classify(client, MODEL, req.text, span)
    except ClassificationError as exc:
        # The model produced something unusable. Surfaced as a failure rather
        # than repaired, so a malformed result can never reach the caller.
        raise HTTPException(
            status_code=502, detail=_failure(captured, str(exc))
        ) from exc
    except OpenAIError as exc:
        raise HTTPException(
            status_code=502,
            detail=_failure(captured, f"provider call failed: {type(exc).__name__}"),
        ) from exc

    return ClassificationResponse(**result.model_dump(), trace=captured.trace())


# --- question answering over the repo's own docs ---------------------------


class AskRequest(BaseModel):
    # Same rule as /classify: a blank question is a validation error, decided
    # here rather than sent to an embedding model and then to the LLM.
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AskResponse(Answer):
    """The answer, the passages behind it, and the development trace."""

    # Typed, not `dict`, so the shape reaches OpenAPI and from there the
    # front end's generated types. This is the only hole that stopped the
    # published schema describing the whole response.
    trace: TraceDocument


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    """Answer from the indexed documentation. Not streamed: the sources are
    part of the result and are only known once retrieval has finished.

    Reads the index per request, so `python index_docs.py` takes effect on the
    next question with no restart.
    """
    captured = None
    try:
        with chat_span(MODEL) as span:
            captured = span
            result = answer_question(client, MODEL, req.question, span)
    except AnsweringError as exc:
        raise HTTPException(
            status_code=502, detail=_failure(captured, str(exc))
        ) from exc
    except OpenAIError as exc:
        raise HTTPException(
            status_code=502,
            detail=_failure(captured, f"provider call failed: {type(exc).__name__}"),
        ) from exc
    except OperationalError as exc:
        # The index lives in Postgres; if it is not up, say so rather than
        # returning an answer with no sources behind it.
        raise HTTPException(
            status_code=503,
            detail=_failure(
                captured,
                "document index unavailable - is Postgres running? "
                "(docker compose up -d, then python index_docs.py)",
            ),
        ) from exc

    return AskResponse(**result.model_dump(), trace=captured.trace())


@app.get("/retrieval/config")
def retrieval_configuration():
    """How retrieval is configured, and what it is searching.

    Reported by the running process rather than read off disk, because the
    eval scores whatever this server actually did. A file on disk can be ahead
    of a server that has not restarted, and a configuration record that is
    quietly wrong is worse than none - it looks covered.

    `config` is collected by introspection, so a knob added to `answering` or
    `embeddings` appears here without this endpoint being touched.
    `index` describes the corpus that was searched, which moves independently
    of the knobs: re-indexing edited documents changes results without any
    setting changing.
    """
    try:
        with store.connect() as conn:
            index = store.stats(conn)
    except OperationalError:
        index = None
    return {
        "config": retrieval_config(),
        "index": None if index is None else {
            "chunks": index["chunks"],
            "documents": index["documents"],
            "models": index["models"],
            "indexed_at": index["indexed_at"].isoformat() if index["indexed_at"] else None,
        },
    }


@app.get("/documents/{path:path}", response_model=Document)
def get_document(path: str):
    """The source document behind a retrieved passage, as it is on disk now.

    Serves only documents the index knows, which is what makes the path safe:
    it has to already be a row in `doc_chunks`. A document that is indexed but
    has since been deleted comes back with `text: null` and `on_disk: false`
    rather than as an error - the caller can say what happened, which a 404
    would not let it distinguish from a path it made up.
    """
    try:
        with store.connect() as conn:
            document = load_document(conn, path)
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail=_failure(None, "document index unavailable - is Postgres running?"),
        ) from exc

    if document is None:
        raise HTTPException(status_code=404, detail=f"no indexed document at {path!r}")
    return document


# --- the agent loop --------------------------------------------------------
#
# Unlike every other endpoint here, a run is a *resource* rather than a
# response. It has to be, because it can stop and wait for a person: an action
# nobody can see is an action nobody can approve, so the pause has to be
# reachable from outside the process that is paused. `POST /agent` therefore
# starts a run and returns its id, and everything after that is a question
# about a run - what is it waiting for, what did you decide, what did it say.


class AgentRequest(BaseModel):
    # Same rule as /ask and /classify: blank input is decided here, before any
    # model call.
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DecisionRequest(BaseModel):
    """A person's answer to one pending action."""

    approved: bool
    # Carried into the tool result the model receives, so a refusal can say
    # *why* and the model has something to work with beyond "no".
    reason: str | None = None


class PendingApprovalView(BaseModel):
    """What the run intends to do, and how long there is to decide."""

    tool: str
    arguments: dict[str, Any]
    description: str
    # Declared on the server's tool table, not written by the model: what the
    # code will actually do, for whoever has to decide.
    effect: str | None
    requested_at: datetime
    expires_at: datetime
    expires_in_s: float


class RunView(BaseModel):
    """A run, whatever state it is in."""

    run_id: str
    question: str
    status: runs.RunStatus  # type: ignore[valid-type]
    # Set only while status is `awaiting_approval`.
    pending: PendingApprovalView | None = None
    # Set once the run finishes. `result.stop_reason` says how it ended,
    # including `approval_expired` when nobody answered.
    result: AgentRun | None = None
    trace: TraceDocument | None = None
    error: str | None = None


def _utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _view(run: runs.Run) -> RunView:
    pending = None
    if run.pending is not None:
        pending = PendingApprovalView(
            tool=run.pending.tool,
            arguments=run.pending.arguments,
            description=run.pending.description,
            effect=run.pending.effect,
            requested_at=_utc(run.pending.requested_at),
            expires_at=_utc(run.pending.expires_at),
            expires_in_s=max(0.0, round(run.pending.expires_at - time.time(), 1)),
        )
    return RunView(
        run_id=run.id, question=run.question, status=run.status,
        pending=pending, result=run.result, trace=run.trace, error=run.error,
    )


@app.post("/agent", status_code=202, response_model=RunView)
def start_agent_run(req: AgentRequest):
    """Start a run and return its id. Does not wait for it to finish.

    `202`, not `200`: the answer does not exist yet, and for a run that needs
    an action approved it cannot exist until a person has decided. Poll
    `GET /agent/{run_id}` - with `?wait=` to be told rather than to ask.
    """
    return _view(runs.start(client, MODEL, req.question))


# Declared before `/agent/{run_id}`, which would otherwise match "runs".
@app.get("/agent/runs", response_model=list[RunView])
def list_agent_runs():
    """Every live run, newest first. Filter on `status` for the approval queue."""
    return [_view(run) for run in runs.listing()]


@app.get("/agent/{run_id}", response_model=RunView)
def get_agent_run(run_id: str, wait: float = Query(
        0.0, ge=0.0, le=runs.MAX_WAIT_S,
        description="Seconds to block until the run needs the caller again.")):
    """The run as it stands.

    `wait` blocks while the run is busy and returns the moment it wants
    something - an approval - or has finished. The same call therefore serves
    "tell me when there is something to decide" and "tell me when it is done",
    which is the difference between a front end that reacts and one that
    polls. It is capped so a caller cannot hold a thread indefinitely.
    """
    run = runs.get(run_id, wait=wait)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return _view(run)


@app.post("/agent/{run_id}/decision", response_model=RunView)
def decide_agent_run(run_id: str, decision: DecisionRequest):
    """Approve or reject the action a run is waiting on, and release it.

    A rejection is an ordinary outcome, not an error: the model is told the
    action was refused and why, and carries on. A `409` means there was nothing
    to decide - the run is not waiting, it was decided already, or nobody
    answered in time and it has expired.
    """
    try:
        run = runs.decide(run_id, decision.approved, decision.reason)
    except runs.DecisionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    return _view(run)
