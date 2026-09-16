"""The approval gate: what stops, who decides, and what happens after.

Driven by a scripted client and a fake gated tool, so none of this needs a
model or a database. The fake tool matters as much as the fake client: these
tests are about the *gate*, and pinning them to `reindex_document` would make
them fail for reasons that have nothing to do with approval.
"""

import threading
import time

import pytest
from pydantic import BaseModel, ConfigDict

import agent
import runs
from test_agent import FakeClient, _call, _completion


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


@pytest.fixture
def tools(monkeypatch):
    """One gated tool and one read-only tool, plus a record of what ran."""
    ran: list[str] = []

    def _run(client, model, args, span):
        ran.append(args.value)
        return agent.ToolResult(True, None, f"did {args.value}")

    table = {
        "act": agent.Tool(name="act", description="changes something",
                          arguments=EchoArgs, run=_run,
                          requires_approval=True,
                          effect="writes a row"),
        "read": agent.Tool(name="read", description="reads something",
                           arguments=EchoArgs, run=_run),
    }
    monkeypatch.setattr(agent, "TOOLS", table)
    return ran


def _asks_then_answers(tool="act", value="x", times=1):
    """A model that requests `tool` `times` times, then gives up and answers."""
    turns = [_completion(calls=[_call(tool, '{"value": "%s"}' % value,
                                      id=f"c{i}")], finish="tool_calls")
             for i in range(times)]
    return FakeClient(*turns, _completion(content="finished"))


def _approver(verdict, reason=None, record=None, delay=0.0):
    def approve(request):
        if record is not None:
            record.append(request)
        if delay:
            time.sleep(delay)
        return agent.Decision(verdict, reason)
    return approve


# --- the system decides what is gated ---------------------------------------


def test_the_model_cannot_declare_its_own_action_safe(tools):
    """The gate is a flag on the server's tool table. It is not in the schema
    the model is shown, and it is not readable from anything the model sends."""
    schema = agent.tool_schemas()
    act = next(s for s in schema if s["function"]["name"] == "act")
    assert "requires_approval" not in act["function"]["parameters"]["properties"]

    # Trying to send it anyway is a validation failure, not a bypass.
    client = FakeClient(
        _completion(calls=[_call("act", '{"value": "x", "requires_approval": false}')],
                    finish="tool_calls"),
        _completion(content="done"),
    )
    asked = []
    result = agent.run_agent(client, "test-model", "q",
                             approve=_approver("approved", record=asked))
    assert result.steps[0].signal == agent.BAD_ARGUMENTS
    assert asked == [] and tools == []


def test_a_read_only_tool_is_never_put_to_a_person(tools):
    def approve(request):
        raise AssertionError("a read-only tool must not be gated")

    result = agent.run_agent(_asks_then_answers(tool="read"), "test-model", "q",
                             approve=approve)
    assert result.stop_reason == "answered"
    assert tools == ["x"]


def test_the_default_is_refusal(tools):
    """A loop with no approver attached must not be able to act."""
    result = agent.run_agent(_asks_then_answers(), "test-model", "q")
    assert result.steps[0].signal == agent.ACTION_REJECTED
    assert tools == []


# --- what a decision does ---------------------------------------------------


def test_approval_lets_the_action_run(tools):
    asked = []
    result = agent.run_agent(_asks_then_answers(), "test-model", "q",
                             approve=_approver("approved", record=asked))
    assert result.stop_reason == "answered"
    assert tools == ["x"]
    assert result.steps[0].ok is True
    # What the person was shown: the intent, the validated arguments, and the
    # effect declared by the server rather than described by the model.
    assert asked[0].tool == "act"
    assert asked[0].arguments == {"value": "x"}
    assert asked[0].effect == "writes a row"


def test_rejection_is_an_ordinary_outcome(tools):
    result = agent.run_agent(_asks_then_answers(), "test-model", "q",
                             approve=_approver("rejected", "not now"))
    # The run continues and answers; it does not raise and does not stop.
    assert result.stop_reason == "answered"
    assert result.answer == "finished"
    assert tools == []
    step = result.steps[0]
    assert step.signal == agent.ACTION_REJECTED and step.ok is False
    assert "not now" in step.result


def test_the_same_refused_action_is_not_put_to_a_person_twice(tools):
    """Re-asking is the model's idea, so the model does not get to do it."""
    asked = []
    result = agent.run_agent(_asks_then_answers(times=2), "test-model", "q",
                             approve=_approver("rejected", "no", record=asked))
    assert len(asked) == 1, "a person was asked the identical question twice"
    assert [s.signal for s in result.steps] == [agent.ACTION_REJECTED] * 2
    assert "already refused" in result.steps[1].result
    assert tools == []


def test_nobody_answering_stops_the_run(tools):
    result = agent.run_agent(_asks_then_answers(), "test-model", "q",
                             approve=_approver("expired"))
    assert result.stop_reason == "approval_expired"
    assert tools == []
    # Expiry is terminal, so the model never gets a turn to work around it.
    assert "Nothing was changed" in result.answer
    # Still recorded: a run that asked and was never answered is not a run
    # that did nothing.
    assert [s.signal for s in result.steps] == [agent.APPROVAL_EXPIRED]
    assert result.steps[0].arguments == {"value": "x"}


def test_waiting_for_a_person_does_not_spend_the_time_budget(tools, monkeypatch):
    """Otherwise any approval slower than the budget fails on arrival."""
    monkeypatch.setattr(agent, "TIME_BUDGET_S", 0.2)
    result = agent.run_agent(_asks_then_answers(), "test-model", "q",
                             approve=_approver("approved", delay=0.35))
    assert result.stop_reason == "answered"
    assert tools == ["x"]


# --- the trace --------------------------------------------------------------


def test_the_trace_shows_the_pause_the_decision_and_what_followed(tools):
    from tracing import ChatSpan

    span = ChatSpan(model="test-model")
    agent.run_agent(_asks_then_answers(), "test-model", "q", span,
                    approve=_approver("rejected", "too risky"))

    kinds = [e["kind"] for e in span.events]
    assert kinds.index("approval_requested") < kinds.index("approval_decided")
    assert kinds.index("approval_decided") < kinds.index("tool_call")

    requested = next(e for e in span.events if e["kind"] == "approval_requested")
    assert requested["tool"] == "act" and requested["arguments"] == {"value": "x"}
    assert requested["effect"] == "writes a row"

    decided = next(e for e in span.events if e["kind"] == "approval_decided")
    assert decided["verdict"] == "rejected" and decided["reason"] == "too risky"
    assert "waited_ms" in decided

    followed = next(e for e in span.events if e["kind"] == "tool_call")
    assert followed["signal"] == agent.ACTION_REJECTED


def test_a_second_request_records_why_nobody_was_asked(tools):
    from tracing import ChatSpan

    span = ChatSpan(model="test-model")
    agent.run_agent(_asks_then_answers(times=2), "test-model", "q", span,
                    approve=_approver("rejected", "no"))
    skipped = next(e for e in span.events if e["kind"] == "approval_skipped")
    assert skipped["tool"] == "act"


# --- the acting tool's arguments -------------------------------------------


def test_reindex_only_accepts_documents_the_indexer_would_index():
    """Validated in the argument model, so it happens before anyone is asked."""
    from pydantic import ValidationError

    assert agent.ReindexArgs(path="api/README.md").path == "api/README.md"
    assert agent.ReindexArgs(path="./api/README.md").path == "api/README.md"
    for bad in ("../../etc/passwd", "/etc/passwd", "api/main.py", "nope.md"):
        with pytest.raises(ValidationError):
            agent.ReindexArgs(path=bad)


def test_reindex_is_the_only_gated_tool_and_declares_its_effect():
    assert [n for n, t in agent.TOOLS.items() if t.requires_approval] == \
        ["reindex_document"]
    assert agent.TOOLS["reindex_document"].effect


# --- the run registry, which is what makes a pause reachable ----------------


@pytest.fixture(autouse=True)
def _clean_registry():
    runs.reset()
    yield
    runs.reset()


def test_a_run_is_addressable_while_it_waits(tools):
    run = runs.start(_asks_then_answers(), "test-model", "q")
    # Blocks until it wants something, rather than being polled for it.
    waiting = runs.get(run.id, wait=5)
    assert waiting.status == "awaiting_approval"
    assert waiting.pending.tool == "act"
    assert waiting.pending.arguments == {"value": "x"}
    assert waiting.pending.effect == "writes a row"

    runs.decide(run.id, approved=True)
    done = runs.get(run.id, wait=5)
    assert done.status == "done"
    assert done.result.stop_reason == "answered"
    assert done.trace is not None
    assert tools == ["x"]


def test_rejecting_from_outside_carries_the_reason_into_the_run(tools):
    run = runs.start(_asks_then_answers(), "test-model", "q")
    runs.get(run.id, wait=5)
    runs.decide(run.id, approved=False, reason="not on a Friday")
    done = runs.get(run.id, wait=5)
    assert done.status == "done"
    assert "not on a Friday" in done.result.steps[0].result
    assert tools == []


def test_deciding_twice_is_refused(tools):
    run = runs.start(_asks_then_answers(), "test-model", "q")
    runs.get(run.id, wait=5)
    runs.decide(run.id, approved=True)
    runs.get(run.id, wait=5)
    with pytest.raises(runs.DecisionRefused):
        runs.decide(run.id, approved=True)


def test_deciding_on_a_run_that_is_not_waiting_is_refused(tools):
    run = runs.start(FakeClient(_completion(content="no tools needed")),
                     "test-model", "q")
    runs.get(run.id, wait=5)
    with pytest.raises(runs.DecisionRefused):
        runs.decide(run.id, approved=True)


def test_an_unanswered_run_expires_rather_than_waiting_forever(tools, monkeypatch):
    monkeypatch.setattr(runs, "APPROVAL_TIMEOUT_S", 0.2)
    run = runs.start(_asks_then_answers(), "test-model", "q")
    assert runs.get(run.id, wait=5).status == "awaiting_approval"
    # Reached awaiting_approval, then gave up on its own.
    for _ in range(50):
        if run.status == "done":
            break
        time.sleep(0.05)
    assert run.status == "done"
    assert run.result.stop_reason == "approval_expired"
    assert tools == []


def test_an_unknown_run_is_not_found():
    assert runs.get("nope") is None
    assert runs.decide("nope", approved=True) is None


def test_a_failing_run_reports_rather_than_dying_silently(tools):
    class Boom:
        @property
        def chat(self):
            raise RuntimeError("provider exploded")

    run = runs.start(Boom(), "test-model", "q")
    for _ in range(100):
        if run.status in ("done", "failed"):
            break
        time.sleep(0.05)
    assert run.status == "failed"
    assert "provider exploded" in run.error


def test_waiting_is_capped(monkeypatch):
    """A caller cannot pin a thread indefinitely.

    Needs a run that stays *running* - a paused one returns straight away,
    which would pass this test without ever reaching the cap.
    """
    monkeypatch.setattr(runs, "MAX_WAIT_S", 0.3)

    class Slow(FakeClient):
        def _create(self, **kwargs):
            time.sleep(3)
            return super()._create(**kwargs)

    run = runs.start(Slow(_completion(content="eventually")), "test-model", "q")
    started = time.monotonic()
    state = runs.get(run.id, wait=10_000)
    elapsed = time.monotonic() - started
    assert state.status == "running", "precondition: the run must still be busy"
    assert elapsed < 1.5, f"waited {elapsed:.1f}s despite a 0.3s cap"


# --- over HTTP, which is the point of the registry --------------------------


@pytest.fixture
def api(monkeypatch, tools):
    """The app, with the provider replaced by a script."""
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "client", _asks_then_answers())
    return TestClient(main.app)


def test_starting_a_run_returns_immediately_with_an_id(api):
    response = api.post("/agent", json={"question": "do the thing"})
    assert response.status_code == 202, "the answer does not exist yet"
    body = response.json()
    assert body["run_id"] and body["status"] == "running"
    assert body["result"] is None


def test_blank_question_is_refused_before_a_run_starts(api):
    assert api.post("/agent", json={"question": "   "}).status_code == 422
    assert api.get("/agent/runs").json() == []


def test_the_pending_action_is_visible_from_outside_the_process(api):
    run_id = api.post("/agent", json={"question": "q"}).json()["run_id"]
    body = api.get(f"/agent/{run_id}", params={"wait": 5}).json()

    assert body["status"] == "awaiting_approval"
    pending = body["pending"]
    assert pending["tool"] == "act"
    assert pending["arguments"] == {"value": "x"}
    assert pending["effect"] == "writes a row"
    assert pending["expires_in_s"] > 0

    # And it is findable without knowing the id, which is what an approval
    # queue needs.
    queue = [r for r in api.get("/agent/runs").json()
             if r["status"] == "awaiting_approval"]
    assert [r["run_id"] for r in queue] == [run_id]


def test_rejecting_over_http_lets_the_run_finish(api, tools):
    run_id = api.post("/agent", json={"question": "q"}).json()["run_id"]
    api.get(f"/agent/{run_id}", params={"wait": 5})

    decided = api.post(f"/agent/{run_id}/decision",
                       json={"approved": False, "reason": "not today"})
    assert decided.status_code == 200

    body = api.get(f"/agent/{run_id}", params={"wait": 5}).json()
    assert body["status"] == "done"
    assert body["result"]["stop_reason"] == "answered"
    assert "not today" in body["result"]["steps"][0]["result"]
    assert body["trace"] is not None
    assert tools == []


def test_approving_over_http_runs_the_action(api, tools):
    run_id = api.post("/agent", json={"question": "q"}).json()["run_id"]
    api.get(f"/agent/{run_id}", params={"wait": 5})
    api.post(f"/agent/{run_id}/decision", json={"approved": True})
    body = api.get(f"/agent/{run_id}", params={"wait": 5}).json()
    assert body["status"] == "done"
    assert tools == ["x"]


def test_nothing_to_decide_is_a_409_and_an_unknown_run_is_a_404(api):
    run_id = api.post("/agent", json={"question": "q"}).json()["run_id"]
    api.get(f"/agent/{run_id}", params={"wait": 5})
    api.post(f"/agent/{run_id}/decision", json={"approved": True})
    api.get(f"/agent/{run_id}", params={"wait": 5})

    again = api.post(f"/agent/{run_id}/decision", json={"approved": True})
    assert again.status_code == 409

    assert api.get("/agent/nope").status_code == 404
    assert api.post("/agent/nope/decision", json={"approved": True}).status_code == 404


def test_the_wait_parameter_is_bounded_by_the_schema(api):
    run_id = api.post("/agent", json={"question": "q"}).json()["run_id"]
    assert api.get(f"/agent/{run_id}", params={"wait": 10_000}).status_code == 422
    assert api.get(f"/agent/{run_id}", params={"wait": -1}).status_code == 422
