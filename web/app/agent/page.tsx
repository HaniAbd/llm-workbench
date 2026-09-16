"use client";

import { Check, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { cn } from "@/lib/utils";
import ApprovalCard from "../components/ApprovalCard";
import Composer from "../components/Composer";
import Markdown from "../components/Markdown";
import { Label, Notice, type NoticeTone } from "../components/Notice";
import { TraceTrigger } from "../components/TraceDrawer";
import { API_BASE, type Run, type StopReason } from "../lib/api";

/** Watching an agent run, and deciding when it wants to act.
 *
 *  The other three pages make one request and render its reply. A run is not
 *  that: it outlives the request that starts it, it can stop and wait for a
 *  person, and it finishes some time later. So this page follows a resource
 *  rather than awaiting a response.
 *
 *  How the pause reaches you, without a refresh: `GET /agent/{id}?wait=` blocks
 *  server-side until the run needs someone or finishes, so the page is waiting
 *  on a socket rather than asking repeatedly whether anything has happened.
 *  The one case that needs a timer is a run already paused - `wait` returns
 *  immediately then, since the run is not busy - so the page sleeps until the
 *  approval would have expired and looks once more. A decision restarts the
 *  loop straight away.
 *
 *  The steps are not re-rendered here. `TraceBody` already lays out a run's
 *  events, including the kinds this step added, so the page ends at a trace
 *  trigger rather than growing a second timeline that would drift from it. */

/** How a run can end, other than by answering. The distinction the page has to
 *  make: a bound and an expired approval are neither an answer nor an error,
 *  and collapsing them into "something went wrong" loses the only information
 *  that would tell you what to do differently. */
const ENDINGS: Record<
  Exclude<StopReason, "answered">,
  { tone: NoticeTone; title: string; detail: string }
> = {
  approval_expired: {
    tone: "info",
    title: "Expired without a decision.",
    detail:
      "Nobody approved or rejected the action in time, so the run stopped itself and nothing was changed. That is the gate doing its job, not a failure.",
  },
  max_steps: {
    tone: "warn",
    title: "Stopped at a bound — too many steps.",
    detail:
      "The agent kept calling tools without arriving at an answer. What it did try is in the trace.",
  },
  time_budget: {
    tone: "warn",
    title: "Stopped at a bound — out of time.",
    detail:
      "The run exceeded its time budget. Time spent waiting for your decision does not count towards it.",
  },
  repeated_tool_call: {
    tone: "warn",
    title: "Stopped at a bound — no progress.",
    detail:
      "The agent asked for the same tool with the same arguments over and over, so the run was cut short rather than left to loop.",
  },
  empty_response: {
    tone: "warn",
    title: "Stopped — the model said nothing.",
    detail:
      "It returned neither an answer nor a tool call. That is a dead end rather than something worth retrying.",
  },
};

const EXAMPLES = [
  "Re-index api/README.md, then tell me what the docs say about tracing.",
  "How are prompts identified in this repository?",
  "Classify this ticket: 'I was charged twice.' Then say what the docs cover.",
  "The docs are stale. Refresh the index for api/README.md.",
];

type Failure = { title: string; message: string; tone: NoticeTone };

/** One decision this page made, kept so the run reads as a sequence rather
 *  than as whatever it happens to be doing now. The API does not report past
 *  approvals on a live run, so what is shown is what this page witnessed. */
type Decided = { tool: string; approved: boolean; reason: string };

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const id = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(id);
        resolve();
      },
      { once: true },
    );
  });

export default function AgentPage() {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [decisions, setDecisions] = useState<Decided[]>([]);
  const [deciding, setDeciding] = useState(false);
  const [starting, setStarting] = useState(false);
  const [resumeKey, setResumeKey] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef(0);

  const runId = run?.run_id ?? null;
  const live = run?.status === "running" || run?.status === "awaiting_approval";
  const waiting = run?.status === "awaiting_approval";
  const busy = starting || live;

  // Follow the run until it stops needing anyone.
  useEffect(() => {
    if (!runId || !live) return;
    let stopped = false;
    const controller = new AbortController();

    (async function follow() {
      while (!stopped) {
        let next: Run;
        try {
          const res = await fetch(`${API_BASE}/agent/${runId}?wait=25`, {
            signal: controller.signal,
          });
          if (!res.ok) {
            setFailure({
              tone: "danger",
              title: "Lost the run.",
              message: `The API answered ${res.status} when asked about it.`,
            });
            return;
          }
          next = await res.json();
        } catch (err) {
          if (!stopped) {
            setFailure({
              tone: "danger",
              title: "Could not reach the API.",
              message: err instanceof Error ? err.message : String(err),
            });
          }
          return;
        }
        if (stopped) return;
        setRun(next);
        if (next.status === "done" || next.status === "failed") return;
        if (next.status === "awaiting_approval") {
          // A paused run is not busy, so the server returns at once and the
          // waiting falls to us. Sleep until the approval would have lapsed,
          // then look again to pick up the expiry; a decision aborts this.
          const ms = Math.max(1000, (next.pending?.expires_in_s ?? 0) * 1000 + 1500);
          await sleep(ms, controller.signal);
        }
      }
    })();

    return () => {
      stopped = true;
      controller.abort();
    };
  }, [runId, live, resumeKey]);

  // Elapsed time, so a long run looks like it is still going somewhere.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - startedAt.current) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [live]);

  // The pause has to reach you even when this tab is not the one you are
  // looking at. The title is the only channel a page has to a background tab
  // without asking permission for notifications.
  useEffect(() => {
    document.title = waiting ? "● Decide — llm-workbench" : "llm-workbench";
    return () => {
      document.title = "llm-workbench";
    };
  }, [waiting]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setStarting(true);
    setAsked(q);
    setRun(null);
    setFailure(null);
    setDecisions([]);
    setElapsed(0);
    startedAt.current = Date.now();
    try {
      const res = await fetch(`${API_BASE}/agent`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      const body = await res.json();
      if (res.status === 202) {
        setQuestion("");
        setRun(body as Run);
      } else if (res.status === 422) {
        const d = body.detail;
        setFailure({
          tone: "warn",
          title: "Rejected (422).",
          message: `${Array.isArray(d) ? (d[0]?.msg ?? "invalid request") : String(d)} — validation refused this before any model call, so no run was started.`,
        });
      } else {
        setFailure({
          tone: "danger",
          title: `Could not start the run (${res.status}).`,
          message: body?.detail?.message ?? String(body?.detail ?? ""),
        });
      }
    } catch (err) {
      setFailure({
        tone: "danger",
        title: "Could not reach the API.",
        message: `${err instanceof Error ? err.message : String(err)} Is it running on ${API_BASE}?`,
      });
    } finally {
      setStarting(false);
    }
  }

  async function decide(approved: boolean, reason: string) {
    if (!runId || !run?.pending) return;
    const tool = run.pending.tool;
    setDeciding(true);
    try {
      const res = await fetch(`${API_BASE}/agent/${runId}/decision`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ approved, reason: reason.trim() || null }),
      });
      if (res.status === 409) {
        // Nothing to decide any more - it expired, or was decided elsewhere.
        // The run itself is the authority, so go and read it.
        setResumeKey((k) => k + 1);
        return;
      }
      if (!res.ok) {
        setFailure({
          tone: "danger",
          title: `The decision was not accepted (${res.status}).`,
          message: "The run is unchanged.",
        });
        return;
      }
      setDecisions((d) => [...d, { tool, approved, reason: reason.trim() }]);
      setRun((await res.json()) as Run);
      setResumeKey((k) => k + 1);
    } catch (err) {
      setFailure({
        tone: "danger",
        title: "Could not send the decision.",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setDeciding(false);
    }
  }

  const result = run?.status === "done" ? run.result : null;
  const ending = result && result.stop_reason !== "answered"
    ? ENDINGS[result.stop_reason]
    : null;

  return (
    <div className="flex flex-1 flex-col">
      <Conversation className="flex-1">
        <ConversationContent className="mx-auto w-full max-w-3xl px-5 pb-48 pt-8">
          {!asked && !failure ? (
            <div className="flex flex-col items-center gap-6 py-20">
              <ConversationEmptyState
                title="Give the agent a job"
                description="It picks its own tools and works in steps. Before anything it does would change stored data, it stops and asks you."
              />
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => setQuestion(q)}
                    className="max-w-full truncate rounded-full border border-border bg-card/60 px-3.5 py-1.5 text-xs text-muted-foreground transition-all hover:border-primary/50 hover:text-foreground active:scale-95"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-5">
              {asked && (
                <div className="flex flex-col gap-2">
                  <Label className="text-primary/80">asked</Label>
                  <p className="leading-7 text-foreground/90">{asked}</p>
                </div>
              )}

              {failure && (
                <Notice tone={failure.tone} title={failure.title}>
                  {failure.message}
                </Notice>
              )}

              {/* What it is doing, now. Not a spinner on its own: a run that
                  is paused and a run that is thinking are different states and
                  only one of them wants you. */}
              {live && (
                <div className="flex items-center gap-2.5 text-sm text-muted-foreground">
                  <span
                    className={cn(
                      "size-1.5 rounded-full",
                      waiting ? "animate-pulse bg-warn" : "animate-pulse bg-primary",
                    )}
                  />
                  {waiting ? "paused — waiting for your decision" : "working…"}
                  <span className="font-mono text-[11px] tabular-nums text-muted-foreground/60">
                    {elapsed}s
                  </span>
                </div>
              )}

              {/* Decisions already made in this run. Rejections are recorded
                  the same way approvals are - neither is an error. */}
              {decisions.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  <Label>decisions</Label>
                  {decisions.map((d, i) => (
                    <div key={i} className="flex items-baseline gap-2 text-[13px]">
                      {d.approved ? (
                        <Check className="size-3.5 shrink-0 translate-y-0.5 text-ok" />
                      ) : (
                        <X className="size-3.5 shrink-0 translate-y-0.5 text-muted-foreground" />
                      )}
                      <span className="text-foreground/85">
                        You {d.approved ? "approved" : "refused"}{" "}
                        <span className="font-mono text-xs">{d.tool}</span>
                        {d.reason && (
                          <span className="text-muted-foreground"> — “{d.reason}”</span>
                        )}
                        {!d.approved && (
                          <span className="text-muted-foreground">
                            {" "}· the agent was told and carried on
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {run?.pending && waiting && (
                <>
                  {/* Keyed on the action, so a second approval in the same
                      run gets a fresh countdown rather than inheriting the
                      first one's. */}
                  <ApprovalCard
                    key={run.pending.requested_at}
                    action={run.pending}
                    question={asked}
                    onDecide={decide}
                    deciding={deciding}
                  />
                  <p className="text-[11px] leading-relaxed text-muted-foreground/70">
                    A paused run reports only what it wants to do next, so any
                    reading or classifying it did first is not shown here. Those
                    steps appear in the trace once the run finishes.
                  </p>
                </>
              )}

              {run?.status === "failed" && (
                <Notice tone="danger" title="The run failed.">
                  {run.error ?? "No reason was reported."}
                </Notice>
              )}

              {ending && (
                <Notice tone={ending.tone} title={ending.title}>
                  {ending.detail}
                </Notice>
              )}

              {result && (
                <div className="flex flex-col gap-4">
                  {/* Only a run that answered has anything the agent wrote.
                      For every other ending `answer` is the loop's own
                      sentence about why it stopped, which the notice above
                      already says - printing it here as well would both
                      repeat it and imply the agent said it. */}
                  {result.stop_reason === "answered" && (
                    <div className="flex flex-col gap-2">
                      <Label>
                        answer · the agent&apos;s words, from what its tools returned
                      </Label>
                      <div className="text-foreground">
                        <Markdown>{result.answer}</Markdown>
                      </div>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-3">
                    {run?.trace && (
                      <TraceTrigger
                        id="agent"
                        label={`agent · ${asked}`}
                        trace={run.trace}
                      />
                    )}
                    {/* The steps live in the trace. This says how many there
                        were so the trigger is worth pressing. */}
                    <span className="font-mono text-[11px] text-muted-foreground/70">
                      {result.steps.length} step
                      {result.steps.length === 1 ? "" : "s"} ·{" "}
                      {result.model_calls} model call
                      {result.model_calls === 1 ? "" : "s"} ·{" "}
                      {result.stop_reason}
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
        </ConversationContent>
        <ConversationScrollButton className="bottom-40" />
      </Conversation>

      <Composer
        value={question}
        onChange={setQuestion}
        onSubmit={submit}
        busy={busy}
        placeholder="Give the agent something to do…"
        hint={
          waiting
            ? "The agent is waiting on your decision above"
            : "It chooses its own tools, and stops before it changes anything"
        }
      />
    </div>
  );
}
