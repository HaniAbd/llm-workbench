import json
import os
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, Field, StringConstraints, model_validator

import prompts
from classification import ClassificationError, ClassificationResult, classify
from tracing import chat_span

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

            for chunk in stream:
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        span.first_token()
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

            yield sse("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- ticket classification -------------------------------------------------


class ClassifyRequest(BaseModel):
    # Blank input is a validation error, not a classification: it is decided
    # here rather than by the model, and costs no call. strip_whitespace makes
    # "   " and "\n\t" fail the same way "" does.
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@app.post("/classify", response_model=ClassificationResult)
def classify_ticket(req: ClassifyRequest):
    """Classify one support ticket. Whole object or an error — never partial.

    Not streamed: the caller wants a complete result, and a half-received
    object cannot be validated against the schema.
    """
    try:
        with chat_span(MODEL) as span:
            return classify(client, MODEL, req.text, span)
    except ClassificationError as exc:
        # The model produced something unusable. Surfaced as a failure rather
        # than repaired, so a malformed result can never reach the caller.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except OpenAIError as exc:
        raise HTTPException(
            status_code=502, detail=f"provider call failed: {type(exc).__name__}"
        ) from exc
