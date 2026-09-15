import json
import os
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI, OpenAIError
from psycopg import OperationalError
from pydantic import BaseModel, Field, StringConstraints, model_validator

import prompts
import store
from agent import AgentError, AgentRun, run_agent
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


class AgentRequest(BaseModel):
    # Same rule as /ask and /classify: blank input is decided here, before any
    # model call.
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AgentResponse(AgentRun):
    """What the loop did, the steps it took, and the trace behind it."""

    trace: TraceDocument


@app.post("/agent", response_model=AgentResponse)
def agent(req: AgentRequest):
    """Answer by choosing capabilities, in sequence, until done or out of room.

    Unlike /ask and /classify this endpoint spans several model calls, so its
    trace covers a whole run: `events` holds one `model_turn` per call and one
    `tool_call` per capability invoked, and the token counts are run totals.
    Each tool that calls the model still opens its own span, so it also leaves
    its own `llm_call` log line.

    Note what is *not* an error here. A capability that fails - including the
    index being unreachable, which /ask reports as a 503 - comes back as a tool
    result the model is expected to read and act on, so the run continues and
    returns 200. Only the loop's own model call failing is a 502: without the
    model there is no loop. Hitting a bound is likewise a 200 with
    `stop_reason` set, never an exception.
    """
    captured = None
    try:
        with chat_span(MODEL) as span:
            captured = span
            result = run_agent(client, MODEL, req.question, span)
    except AgentError as exc:
        raise HTTPException(
            status_code=502, detail=_failure(captured, str(exc))
        ) from exc
    except OpenAIError as exc:
        raise HTTPException(
            status_code=502,
            detail=_failure(captured, f"provider call failed: {type(exc).__name__}"),
        ) from exc

    return AgentResponse(**result.model_dump(), trace=captured.trace())
