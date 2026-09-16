"""Agent runs that outlive a single request, so a person can decide about them.

The loop in `agent.py` can pause and wait; this is what it waits *on*. A run
executes on its own thread and is addressable by id, so the pause is visible
and decidable from outside the process that is paused - which is the whole
point: a front end cannot approve an action it has no way to see.

Why a background thread and not a held-open request. The obvious cheaper design
is to let `POST /agent` block until the person answers. It works, but it spends
a connection and a threadpool slot per paused run for however long someone
takes to look, and it forces the browser to hold a request open for minutes
while polling a second endpoint to find out what it is even waiting for. Here
the request returns immediately with an id, and every later question - what is
it waiting for, what did it decide, what did it answer - is a fast call.

What this is *not* is durable. The registry is a dict in memory: restart the
process and pending approvals are gone. That is the right trade for a local
workbench and the wrong one for anything else, and it is the first thing to
change if this ever needs to survive a deploy.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from agent import AgentRun, ApprovalRequest, Decision, run_agent
from tracing import chat_span

# How long a person has. Long enough to read what is being asked and think
# about it, short enough that a forgotten run does not hold a thread all day.
# Expiry is not a decision - it is the absence of one - so it stops the run
# rather than being treated as a rejection the model can work around.
APPROVAL_TIMEOUT_S = 300.0

# Finished runs are kept so the answer can be collected after the fact, then
# dropped. Bounded two ways because either alone fails: time alone lets a busy
# hour grow without limit, count alone keeps one stale run forever.
RUN_RETENTION_S = 3600.0
MAX_FINISHED_RUNS = 100

# Ceiling for the `wait` long-poll. Bounded so a caller cannot pin a thread
# indefinitely, and kept under the usual 30s proxy timeout.
MAX_WAIT_S = 25.0

RunStatus = Literal["running", "awaiting_approval", "done", "failed"]


@dataclass
class Pending:
    """One action waiting on a person."""

    tool: str
    arguments: dict[str, Any]
    description: str
    effect: str | None
    requested_at: float
    expires_at: float
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _decision: Decision | None = field(default=None, repr=False)


@dataclass
class Run:
    id: str
    question: str
    status: RunStatus
    created_at: float
    updated_at: float
    pending: Pending | None = None
    result: AgentRun | None = None
    trace: dict | None = None
    error: str | None = None


# One lock for the whole registry, and a condition on it so `wait` can block
# until something actually changes instead of polling. Every mutation happens
# under `_cv` and notifies, so there is exactly one place state moves.
_cv = threading.Condition()
_runs: dict[str, Run] = {}


def _touch(run: Run, **changes) -> None:
    """Mutate a run and wake anyone waiting on it. Must hold `_cv`."""
    for name, value in changes.items():
        setattr(run, name, value)
    run.updated_at = time.time()
    _cv.notify_all()


def _evict() -> None:
    """Drop finished runs that are old or surplus. Must hold `_cv`."""
    finished = [r for r in _runs.values() if r.status in ("done", "failed")]
    now = time.time()
    stale = {r.id for r in finished if now - r.updated_at > RUN_RETENTION_S}
    surplus = {r.id for r in sorted(finished, key=lambda r: r.updated_at)
               [: max(0, len(finished) - MAX_FINISHED_RUNS)]}
    for run_id in stale | surplus:
        _runs.pop(run_id, None)


def _approver(run: Run) -> Callable[[ApprovalRequest], Decision]:
    """The gate handed to the loop: publish the request, block, report back."""

    def approve(request: ApprovalRequest) -> Decision:
        now = time.time()
        pending = Pending(
            tool=request.tool, arguments=request.arguments,
            description=request.description, effect=request.effect,
            requested_at=now, expires_at=now + APPROVAL_TIMEOUT_S,
        )
        with _cv:
            _touch(run, status="awaiting_approval", pending=pending)

        pending._event.wait(APPROVAL_TIMEOUT_S)

        with _cv:
            # Read the decision rather than trusting the wait's return value:
            # a decision recorded in the instant the timeout elapsed is still
            # a decision, and discarding it would act against what was said.
            decision = pending._decision
            _touch(run, status="running", pending=None)

        return decision or Decision(
            "expired",
            f"nobody decided within {APPROVAL_TIMEOUT_S:.0f}s",
        )

    return approve


def _execute(run: Run, client, model: str) -> None:
    try:
        with chat_span(model) as span:
            result = run_agent(client, model, run.question, span,
                               approve=_approver(run))
            trace = span.trace()
        with _cv:
            _touch(run, status="done", result=result, trace=trace, pending=None)
    except BaseException as exc:  # noqa: BLE001 - a dead thread must still report
        with _cv:
            _touch(run, status="failed", pending=None,
                   error=f"{type(exc).__name__}: {exc}")


def start(client, model: str, question: str) -> Run:
    """Begin a run in the background and return it immediately."""
    run = Run(id=uuid.uuid4().hex[:12], question=question, status="running",
              created_at=time.time(), updated_at=time.time())
    with _cv:
        _evict()
        _runs[run.id] = run
        _cv.notify_all()
    threading.Thread(target=_execute, args=(run, client, model),
                     name=f"agent-{run.id}", daemon=True).start()
    return run


def get(run_id: str, wait: float = 0.0) -> Run | None:
    """The run, optionally waiting until it needs the caller again.

    `wait` blocks while the run is `running` - that is, while it needs nothing
    from anyone - and returns as soon as it is awaiting approval, done or
    failed. One call therefore covers both "tell me when there is something to
    approve" and "tell me when the answer is ready", which is what a front end
    wants and what tight polling would only approximate.
    """
    deadline = time.monotonic() + min(max(wait, 0.0), MAX_WAIT_S)
    with _cv:
        run = _runs.get(run_id)
        if run is None:
            return None
        while run.status == "running":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _cv.wait(remaining)
        return run


def listing() -> list[Run]:
    """Every live run, newest first - the approval queue, among other things."""
    with _cv:
        return sorted(_runs.values(), key=lambda r: r.created_at, reverse=True)


class DecisionRefused(RuntimeError):
    """The decision cannot be applied - nothing is waiting, or it is too late."""


def decide(run_id: str, approved: bool, reason: str | None = None) -> Run | None:
    """Record a person's answer and release the loop."""
    with _cv:
        run = _runs.get(run_id)
        if run is None:
            return None
        pending = run.pending
        if pending is None:
            raise DecisionRefused(
                f"run {run_id} is {run.status}, not waiting for approval")
        if pending._decision is not None:
            raise DecisionRefused("this action has already been decided")
        if time.time() > pending.expires_at:
            raise DecisionRefused("this action expired before anyone decided")

        pending._decision = Decision(
            "approved" if approved else "rejected",
            reason or ("approved by a person" if approved
                       else "refused by a person"),
        )
        pending._event.set()

        # Let the loop pick it up, so the state we hand back reflects the
        # decision rather than the instant before it.
        _cv.wait_for(lambda: run.pending is None, timeout=5.0)
        return run


def reset() -> None:
    """Drop every run. For tests."""
    with _cv:
        _runs.clear()
        _cv.notify_all()
