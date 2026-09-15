"""The trace document is a contract with the web panel. Renaming a key here
breaks a page that nothing else type-checks against this."""

import json

import tracing

def test_trace_matches_its_declared_schema():
    """The trace dict and TraceDocument cannot drift apart.

    There is no hardcoded key list here any more. The old one was a third copy
    of a contract already written twice, so renaming a field left the API, the
    front end and this test each passing against its own idea of the shape.
    """
    span = tracing.ChatSpan(model="m")
    assert set(span.trace()) == set(tracing.TraceDocument.model_fields)


def test_trace_round_trips_through_the_model():
    """What is serialised must validate against the schema that describes it."""
    span = tracing.ChatSpan(model="m")
    span.prompt_id = "p@1"
    span.messages_sent = [{"role": "system", "content": "x"}]
    span.retrieved = [{"source": "a.md", "heading_path": "a.md > b",
                       "text": "t", "score": 0.5}]
    span.raw_output = "{}"
    span.record("request_sent", message_count=1)
    tracing.TraceDocument.model_validate(span.trace())


def test_an_event_may_carry_fields_nobody_declared():
    """The extension point step 6 relies on: a new kind of step needs no
    change to the model, the schema, or the generated TypeScript."""
    span = tracing.ChatSpan(model="m")
    span.record("tool_call", tool="search", args={"q": "x"}, ok=True)
    event = span.trace()["events"][0]
    assert event["kind"] == "tool_call"
    assert event["tool"] == "search" and event["ok"] is True


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
    # Derived from TraceDocument too, so the log and the trace describe the
    # same call by construction rather than by two lists agreeing.
    assert set(line) == {"event", *tracing.LOG_FIELDS}


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
