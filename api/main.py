import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(dotenv_path="../.env")

client = OpenAI(
    base_url=os.environ["OPENAI_BASE_URL"],
    api_key=os.environ["OPENAI_API_KEY"],
)
MODEL = os.environ["MODEL"]

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Message(BaseModel):
    role: str
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
                    yield sse("token", {"text": choice.delta.content})
                if choice.finish_reason:
                    yield sse("finish", {"reason": choice.finish_reason})

            if chunk.usage:
                yield sse(
                    "usage",
                    {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    },
                )

        yield sse("done", {})

    return StreamingResponse(generate(), media_type="text/event-stream")