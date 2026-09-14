"""One structured log line per completed LLM call.

`ChatSpan` is deliberately shaped like an OpenTelemetry span or a Langfuse
generation: you open it, mark events on it as they happen, and it emits once
on close. Swapping in a real tracing library means reimplementing `chat_span`
and `_emit` here — the call sites in the handler stay as they are.

Nothing in this module knows about OpenAI or FastAPI.
"""

import json
import logging
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

_logger = logging.getLogger("llm.trace")


def _configure() -> None:
    """Own the handler rather than inheriting one.

    uvicorn configures handlers for its own loggers but leaves the root logger
    bare, so a plain `getLogger(...).info(...)` here would be dropped silently.
    Attaching a handler with a bare `%(message)s` formatter is also what keeps
    each record a single clean JSON line that `jq` can read.
    """
    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False


_configure()


@dataclass
class ChatSpan:
    model: str
    # Which prompt produced this call. Set by the caller, carried into the log
    # line so a recorded result can be attributed to an exact prompt text.
    prompt_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    error: str | None = None

    # What the model was actually sent, and what it said before anything
    # reformatted it. Neither is in the log line - they are unbounded, and a
    # log line has to stay one greppable row.
    messages_sent: list[dict] | None = None
    raw_output: str | None = None

    # Ordered, typed steps. This is the growth path: retrieval, tool calls and
    # agent steps append here without changing any field above or any consumer
    # that does not know about them.
    events: list[dict] = field(default_factory=list)

    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _ttft: float | None = field(default=None, repr=False)

    def record(self, kind: str, **data: object) -> None:
        """Append one step, stamped with how far into the call it happened."""
        self.events.append(
            {
                "at_ms": round((time.perf_counter() - self._t0) * 1000),
                "kind": kind,
                **data,
            }
        )

    def trace(self) -> dict:
        """The document handed to the browser.

        A superset of the log line. The log stays scalar and greppable; this
        carries the bulky parts - the messages, the raw reply, the step list -
        because a development panel wants exactly what a log file should not.
        """
        return {
            "model": self.model,
            "prompt_id": self.prompt_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "ttft_ms": self.ttft_ms,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
            "error": self.error,
            "messages_sent": self.messages_sent,
            "raw_output": self.raw_output,
            "events": self.events,
        }

    def first_token(self) -> None:
        """Mark the first content token. Idempotent, so it can be called on
        every token without the handler tracking whether it is the first."""
        if self._ttft is None:
            self._ttft = time.perf_counter() - self._t0

    def set_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def ttft_ms(self) -> int | None:
        return None if self._ttft is None else round(self._ttft * 1000)

    @property
    def latency_ms(self) -> int:
        return round((time.perf_counter() - self._t0) * 1000)


def _emit(span: ChatSpan) -> None:
    _logger.info(
        json.dumps(
            {
                "event": "llm_call",
                "model": span.model,
                "prompt_id": span.prompt_id,
                "input_tokens": span.input_tokens,
                "output_tokens": span.output_tokens,
                "ttft_ms": span.ttft_ms,
                "latency_ms": span.latency_ms,
                "finish_reason": span.finish_reason,
                "error": span.error,
            }
        )
    )


@contextmanager
def chat_span(model: str) -> Iterator[ChatSpan]:
    """Time one LLM call and emit exactly one log line when it closes.

    The line is written from a `finally`, so a call that fails still leaves a
    record: `error` holds the exception class name and the fields that never
    arrived stay null.

    Aborted calls are the exception. Starlette iterates this sync generator in
    a threadpool and abandons it when the client disconnects rather than
    closing it, so `finally` never runs and no line is written (measured: none
    after 60s). Completed and failed calls are logged; aborted ones are not.
    """
    span = ChatSpan(model=model)
    try:
        yield span
    except BaseException as exc:
        span.error = type(exc).__name__
        raise
    finally:
        _emit(span)
