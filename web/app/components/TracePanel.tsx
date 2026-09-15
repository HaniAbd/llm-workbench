"use client";

import type { Trace, TraceEvent } from "../lib/api";

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  // min-w-0 is the load-bearing part: a grid item defaults to min-width:auto,
  // so without it the cell refuses to shrink below its content and a long
  // value (the prompt id) overflows into the next column instead of
  // truncating. The full string stays available on hover, and the panel
  // header prints the prompt id in full regardless.
  return (
    <div className="flex min-w-0 flex-col">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <span
        title={typeof value === "string" ? value : undefined}
        className="truncate font-mono text-xs text-black dark:text-zinc-100"
      >
        {value ?? "—"}
      </span>
    </div>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      {children}
    </div>
  );
}

/** Renders any event. Fields beyond at_ms/kind are shown generically so a
 *  future retrieval or tool-call step needs no change here. */
function EventRow({ event }: { event: TraceEvent }) {
  const { at_ms, kind, ...rest } = event;
  const extra = Object.entries(rest);
  return (
    <li className="flex gap-2 border-l-2 border-zinc-300 py-0.5 pl-2 dark:border-zinc-700">
      <span className="w-14 shrink-0 text-right font-mono text-xs text-zinc-500 dark:text-zinc-400">
        {at_ms}ms
      </span>
      <div className="min-w-0">
        <span className="font-mono text-xs text-black dark:text-zinc-100">{kind}</span>
        {extra.length > 0 && (
          <pre className="mt-0.5 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
            {extra.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n")}
          </pre>
        )}
      </div>
    </li>
  );
}

export default function TracePanel({ trace }: { trace: Trace }) {
  return (
    <details className="rounded border border-black/[.08] bg-white dark:border-white/[.145] dark:bg-black">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs text-zinc-600 dark:text-zinc-400">
        trace — {trace.prompt_id ?? "no prompt"} · {trace.latency_ms}ms
        {trace.error && (
          <span className="ml-2 font-medium text-red-600 dark:text-red-400">
            {trace.error}
          </span>
        )}
      </summary>

      <div className="flex flex-col gap-4 border-t border-black/[.08] px-3 py-3 dark:border-white/[.145]">
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
          <Stat label="model" value={trace.model} />
          <Stat label="prompt" value={trace.prompt_id} />
          <Stat label="tokens in" value={trace.input_tokens} />
          <Stat label="tokens out" value={trace.output_tokens} />
          <Stat label="ttft" value={trace.ttft_ms === null ? "—" : `${trace.ttft_ms}ms`} />
          <Stat label="latency" value={`${trace.latency_ms}ms`} />
          <Stat label="finish" value={trace.finish_reason} />
          <Stat
            label="error"
            value={
              trace.error ? (
                <span className="text-red-600 dark:text-red-400">{trace.error}</span>
              ) : (
                "none"
              )
            }
          />
        </div>

        {trace.retrieved && trace.retrieved.length > 0 && (
          <Block label={`retrieved (${trace.retrieved.length} passages, best first)`}>
            <div className="flex flex-col gap-2">
              {trace.retrieved.map((r, i) => (
                <div key={i}>
                  <div className="flex items-baseline gap-2">
                    <span className="font-mono text-xs text-black dark:text-zinc-100">
                      {r.score.toFixed(3)}
                    </span>
                    {/* A bar makes the gap between a good and a weak match
                        obvious at a glance, which the number alone does not. */}
                    <span className="h-1 w-24 shrink-0 overflow-hidden rounded bg-black/[.08] dark:bg-white/[.145]">
                      <span
                        className="block h-full bg-zinc-500 dark:bg-zinc-400"
                        style={{ width: `${Math.max(0, Math.min(1, r.score)) * 100}%` }}
                      />
                    </span>
                    <span className="truncate font-mono text-[11px] text-zinc-600 dark:text-zinc-400">
                      {r.heading_path}
                    </span>
                  </div>
                  <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-black/[.04] p-2 font-mono text-[11px] leading-5 text-zinc-800 dark:bg-white/[.06] dark:text-zinc-200">
                    {r.text}
                  </pre>
                </div>
              ))}
            </div>
          </Block>
        )}

        {trace.messages_sent && (
          <Block label={`sent (${trace.messages_sent.length} messages)`}>
            <div className="flex flex-col gap-2">
              {trace.messages_sent.map((m, i) => (
                <div key={i}>
                  <span className="font-mono text-[10px] uppercase text-zinc-500 dark:text-zinc-400">
                    {m.role} · {m.content.length} chars
                  </span>
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/[.04] p-2 font-mono text-[11px] leading-5 text-zinc-800 dark:bg-white/[.06] dark:text-zinc-200">
                    {m.content}
                  </pre>
                </div>
              ))}
            </div>
          </Block>
        )}

        {trace.raw_output !== null && (
          <Block label="raw model output (before any reformatting)">
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/[.04] p-2 font-mono text-[11px] leading-5 text-zinc-800 dark:bg-white/[.06] dark:text-zinc-200">
              {trace.raw_output || "(empty)"}
            </pre>
          </Block>
        )}

        {trace.events.length > 0 && (
          <Block label="steps">
            <ul className="flex flex-col gap-0.5">
              {trace.events.map((e, i) => (
                <EventRow key={i} event={e} />
              ))}
            </ul>
          </Block>
        )}
      </div>
    </details>
  );
}
