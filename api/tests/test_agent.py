"""The agent loop, driven by a scripted client so no model is needed.

What is worth testing here is not that the loop calls a tool - a live run
shows that - but that it *terminates*, and that the three things a small model
does wrong become results rather than exceptions. Those are exactly the paths a
live run cannot be relied on to reach: you cannot make llama3.2 invent a tool
name on demand, but you can script one.
"""

from types import SimpleNamespace

import pytest
from openai import APIError

import agent


def _call(name, arguments, id="call_1"):
    return SimpleNamespace(
        id=id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _completion(content=None, calls=None, finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=calls),
            finish_reason=finish,
        )],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


class FakeClient:
    """Replays scripted completions; the last one repeats forever."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


class ExplodingClient:
    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        raise APIError("boom", request=None, body=None)


def run(client):
    return agent.run_agent(client, "test-model", "a question")


# --- terminating ------------------------------------------------------------


def test_plain_reply_ends_the_run():
    result = run(FakeClient(_completion(content="the answer")))
    assert result.stop_reason == "answered"
    assert result.answer == "the answer"
    assert result.steps == []


def test_max_steps_is_a_reported_outcome_not_an_exception(monkeypatch):
    """A model that never stops calling tools must still return."""
    client = FakeClient(_completion(
        calls=[_call("search_docs", '{"question": "q"}')], finish="tool_calls"))
    # Every turn asks for the same thing, so the repetition bound would fire
    # first; widen it so this test is actually about MAX_STEPS.
    monkeypatch.setattr(agent, "MAX_REPEATED_CALLS", 10_000)
    result = run(client)
    assert result.stop_reason == "max_steps"
    assert client.calls == agent.MAX_STEPS
    assert "steps" in result.answer


def test_repeated_identical_call_stops_before_max_steps():
    client = FakeClient(_completion(
        calls=[_call("search_docs", '{"question": "same"}')], finish="tool_calls"))
    result = run(client)
    assert result.stop_reason == "repeated_tool_call"
    # Bounded by repetition, which trips sooner than the step bound.
    assert len(result.steps) == agent.MAX_REPEATED_CALLS
    assert client.calls < agent.MAX_STEPS


def test_time_budget_is_a_reported_outcome(monkeypatch):
    monkeypatch.setattr(agent, "TIME_BUDGET_S", -1.0)
    client = FakeClient(_completion(content="never reached"))
    result = run(client)
    assert result.stop_reason == "time_budget"
    assert client.calls == 0


def test_empty_reply_is_reported_rather_than_retried():
    result = run(FakeClient(_completion(content="   ")))
    assert result.stop_reason == "empty_response"


def test_every_stop_reason_is_published_in_the_schema():
    """A bound the caller cannot name is not a reported outcome."""
    import json
    from pathlib import Path
    schemas = json.loads(
        (Path(__file__).resolve().parent.parent / "openapi.json").read_text()
    )["components"]["schemas"]
    published = set(schemas["AgentResponse"]["properties"]["stop_reason"]["enum"])
    assert published == set(agent.StopReason.__args__)
    assert set(agent._BOUND_MESSAGES) == published - {"answered"}


# --- the model misbehaving --------------------------------------------------


def test_unknown_tool_is_a_result_not_a_crash():
    client = FakeClient(
        _completion(calls=[_call("delete_everything", "{}")], finish="tool_calls"),
        _completion(content="understood"),
    )
    result = run(client)
    assert result.stop_reason == "answered"
    step = result.steps[0]
    assert step.signal == agent.NO_SUCH_TOOL
    # The model is told what does exist, so it can correct itself.
    assert "search_docs" in step.result


@pytest.mark.parametrize("raw", [
    # The failure actually observed from llama3.2: the parameter's own schema
    # echoed back in place of a value.
    '{"question": {"type": "string", "value": "hi"}}',
    '{"question": "hi", "q": "extra key"}',   # undeclared argument
    '{"question": 7}',                        # wrong type
    'not json at all',                        # unparseable
    '[]',                                     # JSON, but not an object
    '{}',                                     # required argument missing
])
def test_nonsense_arguments_are_reported_not_raised(raw):
    client = FakeClient(
        _completion(calls=[_call("search_docs", raw)], finish="tool_calls"),
        _completion(content="gave up"),
    )
    result = run(client)
    step = result.steps[0]
    assert step.signal == agent.BAD_ARGUMENTS
    assert step.arguments is None
    # The raw text is kept verbatim: with a small model it is the finding.
    assert step.raw_arguments == raw
    assert "question" in step.result


def test_a_failing_capability_does_not_kill_the_loop(monkeypatch):
    def explode(*args, **kwargs):
        raise agent.AnsweringError("index on fire")

    monkeypatch.setattr(agent, "answer_question", explode)
    client = FakeClient(
        _completion(calls=[_call("search_docs", '{"question": "q"}')],
                    finish="tool_calls"),
        _completion(content="I could not look that up."),
    )
    result = run(client)
    assert result.stop_reason == "answered"
    assert result.steps[0].signal == agent.TOOL_FAILED
    assert result.steps[0].ok is False


def test_index_being_down_is_a_signal_here_not_a_503(monkeypatch):
    """/ask reports an unreachable index as 503. The agent turns it into a
    tool result instead, so the model can say so and carry on."""
    from psycopg import OperationalError

    def explode(*args, **kwargs):
        raise OperationalError("connection refused")

    monkeypatch.setattr(agent, "answer_question", explode)
    client = FakeClient(
        _completion(calls=[_call("search_docs", '{"question": "q"}')],
                    finish="tool_calls"),
        _completion(content="the index is down"),
    )
    result = run(client)
    assert result.steps[0].signal == agent.INDEX_UNAVAILABLE


def test_retrievals_found_nothing_signal_is_retrievals_own(monkeypatch):
    """Not a second name for the same thing - the loop reuses the sentinel
    retrieval already publishes."""
    import answering
    assert agent.NOT_IN_DOCS == answering.REFUSAL_SENTINEL


def test_loop_model_failure_is_raised_for_the_route_to_turn_into_502():
    with pytest.raises(agent.AgentError):
        run(ExplodingClient())


# --- the trace --------------------------------------------------------------


def test_every_step_reaches_the_trace():
    from tracing import ChatSpan

    span = ChatSpan(model="test-model")
    client = FakeClient(
        _completion(calls=[_call("nope", "{}")], finish="tool_calls"),
        _completion(content="done"),
    )
    agent.run_agent(client, "test-model", "q", span)

    kinds = [e["kind"] for e in span.events]
    assert kinds[0] == "agent_start"
    assert kinds[-1] == "agent_stop"
    assert "model_turn" in kinds and "tool_call" in kinds

    tool_call = next(e for e in span.events if e["kind"] == "tool_call")
    # what was chosen, with what arguments, and what came back
    assert tool_call["tool"] == "nope"
    assert tool_call["raw_arguments"] == "{}"
    assert tool_call["signal"] == agent.NO_SUCH_TOOL

    stop = span.events[-1]
    assert stop["stop_reason"] == "answered"
    # Token counts are run totals across every model turn.
    assert span.input_tokens == 20


def test_trace_records_the_bound_that_stopped_the_run():
    from tracing import ChatSpan

    span = ChatSpan(model="test-model")
    client = FakeClient(_completion(
        calls=[_call("search_docs", '{"question": "same"}')], finish="tool_calls"))
    agent.run_agent(client, "test-model", "q", span)
    bound = next(e for e in span.events if e["kind"] == "bound_hit")
    assert bound["bound"] == "repeated_tool_call"


def test_tool_schemas_are_derived_from_the_argument_models():
    """The schema advertised and the validator applied are one object, so they
    cannot describe different arguments."""
    schemas = {s["function"]["name"]: s["function"]["parameters"]
               for s in agent.tool_schemas()}
    assert set(schemas) == set(agent.TOOLS)
    assert schemas["search_docs"]["additionalProperties"] is False
    assert schemas["search_docs"]["required"] == ["question"]
