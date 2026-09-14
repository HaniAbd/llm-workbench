import json
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

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
    messages: list[Message]


SYSTEM_PROMPT = "You are concise and factual."


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [m.model_dump() for m in req.messages]

    def generate():
        # The span opens before the request so latency covers the whole call,
        # and closes after the last yield so the log line is written once the
        # stream completes. Marking it costs nothing per token.
        with chat_span(MODEL) as span:
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