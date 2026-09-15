"use client";

import { Check } from "lucide-react";
import { useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { cn } from "@/lib/utils";
import Composer from "../components/Composer";
import { Label, Notice } from "../components/Notice";
import Markdown from "../components/Markdown";
import { isBelowFloor, useSimilarityFloor } from "../components/RetrievalConfig";
import { TraceTrigger } from "../components/TraceDrawer";
import { API_BASE, type AskResult, type Trace } from "../lib/api";

type Outcome =
  | { kind: "ok"; result: AskResult }
  | { kind: "rejected"; message: string } // 422
  | { kind: "no-index"; message: string; trace: Trace | null } // 503
  | { kind: "failed"; message: string; trace: Trace | null } // 502
  | { kind: "unreachable"; message: string };


/** Does the answer quote this exact heading path?
 *
 *  A substring test against a known string, deliberately: heading paths
 *  themselves contain backticks, so pulling citation-shaped text back out of
 *  the prose is unreliable. Searching for paths we already know is not. */
const quotedIn = (answer: string, headingPath: string) => answer.includes(headingPath);

/** The answer appears to cite something - a file path or a heading chain. */
const looksLikeACitation = (answer: string) =>
  answer.includes(".md") || answer.includes(" > ");

const EXAMPLES = [
  "Why is the eval suite not run in CI?",
  "How are prompts identified?",
  "What makes two eval runs comparable?",
  "Name one sea.",
];

export default function AskPage() {
  // The floor comes from the API that enforces it, not from a number kept
  // here. See components/RetrievalConfig.tsx.
  const { floor } = useSimilarityFloor();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setAsked(q);
    setOutcome(null);
    try {
      const res = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
      const body = await res.json();
      if (res.ok) {
        setOutcome({ kind: "ok", result: body as AskResult });
      } else if (res.status === 422) {
        const d = body.detail;
        setOutcome({
          kind: "rejected",
          message: Array.isArray(d) ? (d[0]?.msg ?? "invalid request") : String(d),
        });
      } else {
        const d = body.detail ?? {};
        setOutcome({
          kind: res.status === 503 ? "no-index" : "failed",
          message: d.message ?? `HTTP ${res.status}`,
          trace: d.trace ?? null,
        });
      }
    } catch (err) {
      setOutcome({
        kind: "unreachable",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <Conversation className="flex-1">
        <ConversationContent className="mx-auto w-full max-w-3xl px-5 pb-48 pt-8">
          {!asked && !outcome ? (
            <div className="flex flex-col items-center gap-6 py-20">
              <ConversationEmptyState
                title="Ask the documentation"
                description="Answers come only from this repository's markdown, retrieved by vector similarity. Every answer shows the passages behind it."
              />
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => setQuestion(q)}
                    className="rounded-full border border-border bg-card/60 px-3.5 py-1.5 text-xs text-muted-foreground transition-all hover:border-primary/50 hover:text-foreground active:scale-95"
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
                  <Label className="text-primary/80">question</Label>
                  <p className="leading-7 text-foreground/90">{asked}</p>
                </div>
              )}

              {busy && (
                <div className="flex items-center gap-2.5 text-sm text-muted-foreground">
                  <span className="size-1.5 animate-pulse rounded-full bg-primary" />
                  searching the documentation…
                </div>
              )}

              {outcome?.kind === "rejected" && (
                <Notice tone="warn" title="Rejected (422).">
                  {outcome.message} Validation refused this before any model call,
                  so no tokens were spent and there is no trace to show.
                </Notice>
              )}

              {outcome?.kind === "no-index" && (
                <Notice tone="warn" title="Document index unreachable (503).">
                  {outcome.message} This is a retrieval failure, not a bad answer
                  — no passages could be looked up at all, so nothing was asked
                  of the model.
                </Notice>
              )}

              {outcome?.kind === "unreachable" && (
                <Notice tone="danger" title="Could not reach the API.">
                  {outcome.message} Is it running on {API_BASE}?
                </Notice>
              )}

              {outcome?.kind === "failed" && (
                <>
                  <Notice tone="danger" title="Failed (502).">
                    {outcome.message}
                  </Notice>
                  {outcome.trace && (
                    <TraceTrigger id="ask-failed" label="ask · failed call" trace={outcome.trace} />
                  )}
                </>
              )}

              {outcome?.kind === "ok" &&
                (() => {
                  const { answered, answer, sources, trace } = outcome.result;
                  // The top passage cleared the API's floor by definition -
                  // below it the request would have been refused. What is
                  // worth saying is how much of the SUPPORTING material did
                  // not clear it.
                  const belowFloor = sources.filter((s) => isBelowFloor(s.score, floor));
                  const thinSupport = floor !== null && belowFloor.length > 0;
                  const quoted = sources.filter((s) => quotedIn(answer, s.heading_path));
                  const citesNothingReal =
                    answered && looksLikeACitation(answer) && quoted.length === 0;

                  return (
                    <div className="flex flex-col gap-4">
                      {sources.length === 0 && (
                        <Notice tone="warn" title="Nothing retrieved.">
                          The index returned no passages — it is probably empty.
                          Run <code className="font-mono">python index_docs.py</code>{" "}
                          in <code className="font-mono">api/</code>.
                        </Notice>
                      )}

                      {thinSupport && (
                        <Notice tone="warn" title="Thin support.">
                          <span className="font-mono">{belowFloor.length}</span> of{" "}
                          <span className="font-mono">{sources.length}</span> passages
                          scored below{" "}
                          <span className="font-mono">{floor}</span>, the floor this
                          API refuses on. The top passage cleared it, so the answer
                          rests mainly on that one.
                        </Notice>
                      )}

                      {/* A refusal is a correct outcome, so it wears `info`
                          rather than the colour a 502 wears. */}
                      {!answered && (
                        <Notice tone="info" title="Not in the documentation.">
                          The system is saying it does not know, which is a correct
                          outcome — not a failure. The passages below were the
                          nearest found, and none of them answers the question.
                        </Notice>
                      )}

                      <div className="flex flex-col gap-2">
                        <Label>
                          {answered
                            ? "answer · the model's words, which may misquote or abbreviate a source"
                            : "what it said"}
                        </Label>
                        <div className="text-foreground">
                          <Markdown>{answer}</Markdown>
                        </div>
                      </div>

                      {citesNothingReal && (
                        <Notice tone="warn" title="Unreliable citation.">
                          The answer cites a source, but none of the retrieved
                          passages below appears in it verbatim — the model has
                          abbreviated or invented the reference. Use the list below
                          instead.
                        </Notice>
                      )}

                      <div className="flex flex-col gap-2 rounded-xl border border-ok/25 bg-ok/[0.06] p-3.5">
                        <div className="flex items-baseline justify-between gap-3">
                          <Label className="text-ok">
                            retrieved · authoritative
                          </Label>
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {sources.length} passages
                          </span>
                        </div>
                        {sources.map((s, i) => {
                          const isQuoted = quotedIn(answer, s.heading_path);
                          return (
                            <div key={i} className="flex items-center gap-2.5">
                              <span
                                className={cn(
                                  "w-12 shrink-0 font-mono text-xs",
                                  floor === null
                                    ? "text-foreground"
                                    : isBelowFloor(s.score, floor)
                                      ? "text-warn"
                                      : "text-ok",
                                )}
                              >
                                {s.score.toFixed(3)}
                              </span>
                              <span className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-muted">
                                <span
                                  className={cn(
                                    "block h-full rounded-full transition-[width] duration-700 ease-out",
                                    floor === null
                                      ? "bg-primary"
                                      : isBelowFloor(s.score, floor)
                                        ? "bg-warn"
                                        : "bg-ok",
                                  )}
                                  style={{
                                    width: `${Math.max(0, Math.min(1, s.score)) * 100}%`,
                                  }}
                                />
                              </span>
                              <span
                                title={
                                  isQuoted ? "quoted verbatim in the answer" : undefined
                                }
                                className="flex w-4 shrink-0 justify-center"
                              >
                                {isQuoted && <Check className="size-3 text-ok" />}
                              </span>
                              <span className="truncate font-mono text-[11px] text-muted-foreground">
                                {s.heading_path}
                              </span>
                            </div>
                          );
                        })}
                        <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground/80">
                          This is what retrieval actually supplied, whatever the
                          answer claims. A tick means the exact path appears in the
                          answer text; a passage can still have been used without
                          being quoted.
                          {floor === null
                            ? " The API's similarity floor could not be read, so scores are shown without a verdict."
                            : ` Scores are judged against the API's floor of ${floor}.`}
                        </p>
                      </div>

                      <TraceTrigger id="ask" label={`ask · ${asked}`} trace={trace} />
                    </div>
                  );
                })()}
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
        placeholder="Ask about this repository…"
        hint="Answers come only from the indexed markdown"
      />
    </div>
  );
}
