"use client";

import { Fragment } from "react";
import { Clock, Coins, FileText, Sparkles, Zap } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Trace, TraceEvent } from "../lib/api";
import { isBelowFloor, useSimilarityFloor } from "./RetrievalConfig";
import { Label } from "./Notice";

/** The contents of a trace, laid out for the drawer.
 *
 *  Nothing here is markdown-rendered. Passages, sent messages and raw output
 *  are records of what actually passed through the system, and a trace that
 *  reformats them is not showing you what happened. See Markdown.tsx for the
 *  line and why it sits there.
 *
 *  Passage scores are coloured against the API's own similarity floor. With no
 *  floor known they stay neutral rather than borrowing a number from here. */

function Stat({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: React.ElementType;
  label: string;
  value: React.ReactNode;
  tone?: "danger";
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        <Icon className="size-3 shrink-0" />
        {label}
      </span>
      <span
        title={typeof value === "string" ? value : undefined}
        className={cn(
          "truncate font-mono text-xs",
          tone === "danger" ? "text-danger" : "text-foreground",
        )}
      >
        {value ?? "—"}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Label>{title}</Label>
      {children}
    </div>
  );
}

const Pre = ({ children }: { children: React.ReactNode }) => (
  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-background/60 p-3 font-mono text-[11px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
    {children}
  </pre>
);

/** One field's value, formatted by what it actually is.
 *
 *  Nothing here knows any step's shape. It dispatches on the runtime type, so
 *  a tool call or an agent step carrying fields this build has never seen
 *  renders the same way as the ones that exist today. Objects nest one level
 *  before falling back to JSON, which keeps a deep payload from pushing the
 *  column apart. */
function Value({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground/60">—</span>;
  }
  if (typeof value === "boolean") {
    return (
      <span className={cn("font-mono", value ? "text-ok" : "text-muted-foreground")}>
        {String(value)}
      </span>
    );
  }
  if (typeof value === "number") {
    return <span className="font-mono tabular-nums text-foreground">{value}</span>;
  }
  if (typeof value === "string") {
    return <span className="break-words text-foreground/90">{value}</span>;
  }
  if (Array.isArray(value)) {
    const scalars = value.every((v) => v === null || typeof v !== "object");
    if (scalars) {
      return (
        <span className="font-mono text-foreground/90">
          {value.length === 0 ? "[]" : value.map((v) => String(v)).join(", ")}
        </span>
      );
    }
    return <Json value={value} />;
  }
  if (depth < 1) {
    return <Fields fields={value as Record<string, unknown>} depth={depth + 1} />;
  }
  return <Json value={value} />;
}

const Json = ({ value }: { value: unknown }) => (
  <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
    {JSON.stringify(value, null, 1)}
  </pre>
);

/** A step's fields as aligned name/value pairs rather than a dumped blob.
 *
 *  Two columns, so the names form a left edge and the values a second one:
 *  the pairs can be read without parsing `key: value` out of a line. */
function Fields({
  fields,
  depth = 0,
}: {
  fields: Record<string, unknown>;
  depth?: number;
}) {
  const entries = Object.entries(fields);
  if (entries.length === 0) return null;
  return (
    <dl
      className={cn(
        "grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[11px]",
        depth > 0 && "mt-1 border-l border-border/60 pl-2.5",
      )}
    >
      {entries.map(([name, value]) => (
        <Fragment key={name}>
          <dt className="font-mono text-muted-foreground">{name}</dt>
          <dd className="min-w-0">
            <Value value={value} depth={depth} />
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

/** One step on the timeline.
 *
 *  Three columns, fixed: elapsed time, the rail, then the step. The time
 *  column is a fixed width with tabular figures, so 0ms and 10006ms occupy the
 *  same space and the numbers line up instead of drifting as they grow.
 *
 *  The gap from the previous step is printed beneath the absolute time, and
 *  only when there is one - reading a 9-second pause should not require
 *  subtracting two numbers, and a step that lands in the same millisecond as
 *  the last should not claim otherwise. */
function Step({
  event,
  previous,
  first,
  last,
}: {
  event: TraceEvent;
  previous?: TraceEvent;
  first: boolean;
  last: boolean;
}) {
  const { at_ms, kind, ...fields } = event;
  const gap = previous ? at_ms - previous.at_ms : 0;

  return (
    <li className="grid grid-cols-[4.25rem_0.75rem_minmax(0,1fr)] gap-x-2.5">
      <div className="pt-px text-right">
        <div className="font-mono text-[11px] tabular-nums text-foreground">
          {at_ms}
          <span className="text-muted-foreground/70">ms</span>
        </div>
        {gap > 0 && (
          <div className="font-mono text-[10px] tabular-nums text-muted-foreground/60">
            +{gap}
          </div>
        )}
      </div>

      {/* The rail is one continuous line through every dot, stopped at the
          first and last so the sequence reads as bounded rather than trailing
          off. */}
      <div className="relative flex justify-center">
        <span
          aria-hidden
          className={cn(
            "absolute w-px bg-border",
            first && last && "hidden",
            first && !last && "top-[7px] bottom-0",
            !first && !last && "inset-y-0",
            last && !first && "top-0 h-[7px]",
          )}
        />
        <span className="relative mt-[4px] size-1.5 shrink-0 rounded-full bg-primary ring-[3px] ring-card" />
      </div>

      <div className={cn("min-w-0", last ? "pb-0" : "pb-3.5")}>
        <div className="font-mono text-xs text-foreground">{kind}</div>
        <Fields fields={fields} />
      </div>
    </li>
  );
}

export default function TraceBody({ trace }: { trace: Trace }) {
  const { floor } = useSimilarityFloor();
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3">
        <Stat icon={Sparkles} label="model" value={trace.model} />
        <Stat icon={FileText} label="prompt" value={trace.prompt_id} />
        <Stat icon={Coins} label="tokens in" value={trace.input_tokens} />
        <Stat icon={Coins} label="tokens out" value={trace.output_tokens} />
        <Stat
          icon={Zap}
          label="ttft"
          value={trace.ttft_ms === null ? "—" : `${trace.ttft_ms}ms`}
        />
        <Stat icon={Clock} label="latency" value={`${trace.latency_ms}ms`} />
        <Stat icon={Clock} label="finish" value={trace.finish_reason} />
        <Stat
          icon={Zap}
          label="error"
          value={trace.error ?? "none"}
          tone={trace.error ? "danger" : undefined}
        />
      </div>

      {trace.retrieved && trace.retrieved.length > 0 && (
        <Section
          title={
            floor === null
              ? `retrieved · ${trace.retrieved.length} passages, best first`
              : `retrieved · ${trace.retrieved.length} passages, best first · floor ${floor}`
          }
        >
          <div className="flex flex-col gap-3">
            {trace.retrieved.map((r, i) => {
              const weak = isBelowFloor(r.score, floor);
              return (
                <div key={i} className="flex flex-col gap-1.5">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "w-12 shrink-0 font-mono text-xs",
                        floor === null
                          ? "text-foreground"
                          : weak
                            ? "text-warn"
                            : "text-ok",
                      )}
                    >
                      {r.score.toFixed(3)}
                    </span>
                    <span className="h-1 w-20 shrink-0 overflow-hidden rounded-full bg-muted">
                      <span
                        className={cn(
                          "block h-full rounded-full transition-[width] duration-500 ease-out",
                          floor === null ? "bg-primary" : weak ? "bg-warn" : "bg-ok",
                        )}
                        style={{ width: `${Math.max(0, Math.min(1, r.score)) * 100}%` }}
                      />
                    </span>
                    <span className="truncate font-mono text-[11px] text-muted-foreground">
                      {r.heading_path}
                    </span>
                  </div>
                  <Pre>{r.text}</Pre>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {trace.messages_sent && (
        <Section title={`sent · ${trace.messages_sent.length} messages`}>
          <div className="flex flex-col gap-2.5">
            {trace.messages_sent.map((m, i) => (
              <div key={i} className="flex flex-col gap-1">
                <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                  {m.role} · {m.content.length} chars
                </span>
                <Pre>{m.content}</Pre>
              </div>
            ))}
          </div>
        </Section>
      )}

      {trace.raw_output !== null && (
        <Section title="raw model output · before any reformatting">
          <Pre>{trace.raw_output || "(empty)"}</Pre>
        </Section>
      )}

      {trace.events.length > 0 && (
        <Section title="steps">
          <ol className="flex flex-col">
            {trace.events.map((e, i) => (
              <Step
                key={i}
                event={e}
                previous={trace.events[i - 1]}
                first={i === 0}
                last={i === trace.events.length - 1}
              />
            ))}
          </ol>
        </Section>
      )}
    </div>
  );
}
