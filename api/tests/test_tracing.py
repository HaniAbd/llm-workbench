"""The trace document is a contract with the web panel. Renaming a key here
breaks a page that nothing else type-checks against this."""

import json

import tracing

# What web/app/lib/api.ts declares as `Trace`.
PANEL_KEYS = {
    "model", "prompt_id", "input_tokens", "output_tokens", "ttft_ms",
    "latency_ms", "finish_reason", "error", "messages_sent", "raw_output",
    "events",
}


def test_trace_has_exactly_the_keys_the_panel_expects():
    span = tracing.ChatSpan(model="m")
    assert set(span.trace()) == PANEL_KEYS


def test_trace_is_json_serialisable():
    span = tracing.ChatSpan(model="m")
    span.prompt_id = "p@1"
    span.messages_sent = [{"role": "system", "content": "x"}]
    span.raw_output = "{}"
    span.record("request_sent", message_count=1)
    json.dumps(span.trace())


def test_log_line_stays_scalar():
    """The log is one greppable row; the bulky fields belong to the trace."""
    span = tracing.ChatSpan(model="m")
    span.messages_sent = [{"role": "system", "content": "x"}]
    span.raw_output = "y"
    line = json.loads(_emitted(span))
    assert "messages_sent" not in line and "raw_output" not in line and "events" not in line
    assert set(line) == {
        "event", "model", "prompt_id", "input_tokens", "output_tokens",
        "ttft_ms", "latency_ms", "finish_reason", "error",
    }


def _emitted(span):
    import io
    buf = io.StringIO()
    original = tracing._logger.handlers[0].stream
    tracing._logger.handlers[0].stream = buf
    try:
        tracing._emit(span)
    finally:
        tracing._logger.handlers[0].stream = original
    return buf.getvalue()


def test_first_token_is_idempotent():
    span = tracing.ChatSpan(model="m")
    span.first_token()
    first = span.ttft_ms
    span.first_token()
    assert span.ttft_ms == first
