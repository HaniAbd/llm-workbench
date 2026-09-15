"use client";

import { useState } from "react";
import TracePanel from "../components/TracePanel";
import { API_BASE, type AskResult, type Trace } from "../lib/api";

type Outcome =
  | { kind: "ok"; result: AskResult }
  | { kind: "rejected"; message: string } // 422
  | { kind: "no-index"; message: string; trace: Trace | null } // 503
  | { kind: "failed"; message: string; trace: Trace | null } // 502
  | { kind: "unreachable"; message: string };

/** Below this, the best passage is treated as a weak match rather than an
 *  answer. Measured on this corpus: questions the docs actually cover score
 *  0.62-0.72, while "Name one sea" scores 0.466-0.478. 0.55 sits in the gap.
 *  The score is always shown, so the threshold is a hint and not a verdict. */
const WEAK_MATCH_BELOW = 0.55;

/** Does the answer quote this exact heading path?
 *
 *  A substring test against a known string, deliberately: heading paths
 *  themselves contain backticks (`api/README.md > api > \`POST /ask\` > ...`),
 *  so pulling citation-shaped text back out of the prose is unreliable.
 *  Searching for paths we already know is not. */
function quotedIn(answer: string, headingPath: string): boolean {
  return answer.includes(headingPath);
}

/** The answer appears to cite something - a file path or a heading chain. */
function looksLikeACitation(answer: string): boolean {
  return answer.includes(".md") || answer.includes(" > ");
}

const EXAMPLES = [
  "Why is the eval suite not run in CI?",
  "How are prompts identified?",
  "What makes two eval runs comparable?",
  "Name one sea.",
];

export default function AskPage() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setOutcome(null);
    try {
      const res = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
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
    <div className="flex flex-1 flex-col items-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-5 px-6 py-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-black dark:text-zinc-50">
            Ask the docs
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            Answers come only from this repository&apos;s markdown, retrieved by
            vector similarity. Open the trace to see which passages were found
            and how well they matched.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => {
                setQuestion(q);
                setOutcome(null);
              }}
              className="rounded-full border border-black/[.08] px-3 py-1 text-xs text-zinc-700 transition-colors hover:bg-black/[.04] dark:border-white/[.145] dark:text-zinc-300 dark:hover:bg-white/[.06]"
            >
              {q}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about this repository…"
            disabled={busy}
            className="flex-1 rounded-full border border-black/[.08] bg-white px-5 py-3 text-black outline-none placeholder:text-zinc-400 focus:border-black/[.3] disabled:opacity-50 dark:border-white/[.145] dark:bg-black dark:text-zinc-50 dark:focus:border-white/[.4]"
          />
          <button
            type="submit"
            disabled={busy || !question.trim()}
            className="rounded-full bg-foreground px-6 py-3 font-medium text-background transition-colors hover:bg-[#383838] disabled:opacity-40 dark:hover:bg-[#ccc]"
          >
            {busy ? "…" : "Ask"}
          </button>
        </form>

        {outcome?.kind === "rejected" && (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-400">
            <strong className="font-medium">Rejected (422).</strong> {outcome.message}
          </div>
        )}

        {outcome?.kind === "no-index" && (
          <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-400">
            <strong className="font-medium">
              Document index unreachable (503).
            </strong>{" "}
            {outcome.message}
            <div className="mt-1 text-xs opacity-80">
              This is a retrieval failure, not a bad answer — no passages could
              be looked up at all, so nothing was asked of the model.
            </div>
          </div>
        )}

        {outcome?.kind === "unreachable" && (
          <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
            <strong className="font-medium">Could not reach the API.</strong>{" "}
            {outcome.message}
            <div className="mt-1 text-xs opacity-80">Is it running on {API_BASE}?</div>
          </div>
        )}

        {outcome?.kind === "failed" && (
          <div className="flex flex-col gap-2">
            <div className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-700 dark:text-red-400">
              <strong className="font-medium">Failed (502).</strong> {outcome.message}
            </div>
            {outcome.trace && <TracePanel trace={outcome.trace} />}
          </div>
        )}

        {outcome?.kind === "ok" && (() => {
          const { answered, answer, sources, trace } = outcome.result;
          const best = sources.length ? Math.max(...sources.map((s) => s.score)) : 0;
          const weak = sources.length === 0 || best < WEAK_MATCH_BELOW;
          const quoted = sources.filter((s) => quotedIn(answer, s.heading_path));
          const citesNothingReal =
            answered && looksLikeACitation(answer) && quoted.length === 0;

          return (
            <div className="flex flex-col gap-3">
              {sources.length === 0 && (
                <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-400">
                  <strong className="font-medium">Nothing retrieved.</strong> The
                  index returned no passages — it is probably empty. Run{" "}
                  <code className="font-mono">python index_docs.py</code> in{" "}
                  <code className="font-mono">api/</code>.
                </div>
              )}

              {weak && sources.length > 0 && (
                <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-400">
                  <strong className="font-medium">Weak match.</strong> The best
                  passage scored{" "}
                  <span className="font-mono">{best.toFixed(3)}</span>, below{" "}
                  <span className="font-mono">{WEAK_MATCH_BELOW}</span>. Retrieval
                  always returns four passages whether or not any is relevant, so
                  the answer below may be built from unrelated text.
                </div>
              )}

              {!answered && (
                <div className="rounded border border-zinc-400/50 bg-zinc-400/10 px-3 py-2 text-sm text-zinc-700 dark:text-zinc-300">
                  <strong className="font-medium">Not in the documentation.</strong>{" "}
                  The system is saying it does not know, which is a correct
                  outcome — not a failure. The passages below were the nearest
                  found, and none of them answers the question.
                </div>
              )}

              <div className="flex flex-col gap-1">
                <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  {answered
                    ? "answer — the model's words, which may misquote or abbreviate a source"
                    : "what it said"}
                </span>
                <p className="whitespace-pre-wrap leading-7 text-black dark:text-zinc-100">
                  {answer}
                </p>
              </div>

              {citesNothingReal && (
                <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-400">
                  <strong className="font-medium">Unreliable citation.</strong> The
                  answer cites a source, but none of the retrieved passages below
                  appears in it verbatim — the model has abbreviated or invented
                  the reference. Use the list below instead.
                </div>
              )}

              <div className="flex flex-col gap-1">
                <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  retrieved — authoritative: what the model was actually given (
                  {sources.length})
                </span>
                {sources.map((s, i) => {
                  const isQuoted = quotedIn(answer, s.heading_path);
                  return (
                    <div key={i} className="flex items-baseline gap-2 text-xs">
                      <span
                        className={
                          s.score < WEAK_MATCH_BELOW
                            ? "font-mono text-amber-700 dark:text-amber-500"
                            : "font-mono text-zinc-600 dark:text-zinc-400"
                        }
                      >
                        {s.score.toFixed(3)}
                      </span>
                      <span className="h-1 w-16 shrink-0 overflow-hidden rounded bg-black/[.08] dark:bg-white/[.145]">
                        <span
                          className="block h-full bg-zinc-500 dark:bg-zinc-400"
                          style={{ width: `${Math.max(0, Math.min(1, s.score)) * 100}%` }}
                        />
                      </span>
                      <span
                        title={isQuoted ? "quoted verbatim in the answer" : undefined}
                        className={
                          isQuoted
                            ? "w-12 shrink-0 text-[10px] uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
                            : "w-12 shrink-0"
                        }
                      >
                        {isQuoted ? "quoted" : ""}
                      </span>
                      <span className="truncate font-mono text-zinc-800 dark:text-zinc-200">
                        {s.heading_path}
                      </span>
                    </div>
                  );
                })}
                <span className="mt-1 text-[10px] text-zinc-500 dark:text-zinc-400">
                  &ldquo;quoted&rdquo; means this exact path appears in the answer
                  text. A passage can still have been used without being quoted.
                </span>
              </div>

              <TracePanel trace={trace} />
            </div>
          );
        })()}

      </main>
    </div>
  );
}
