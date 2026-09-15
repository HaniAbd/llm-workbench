"use client";

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

/** Renders any step. Fields beyond at_ms/kind are shown generically, so a
 *  future tool call or agent step needs no change here. */
function EventRow({ event }: { event: TraceEvent }) {
  const { at_ms, kind, ...rest } = event;
  const extra = Object.entries(rest);
  return (
    <li className="relative flex gap-3 pl-4">
      <span className="absolute left-0 top-[7px] size-1.5 rounded-full bg-primary/70 ring-4 ring-primary/10" />
      <span className="w-14 shrink-0 text-right font-mono text-[11px] text-muted-foreground">
        {at_ms}ms
      </span>
      <div className="min-w-0 pb-2">
        <span className="font-mono text-xs text-foreground">{kind}</span>
        {extra.length > 0 && (
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] text-muted-foreground">
            {extra.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n")}
          </pre>
        )}
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
          <ul className="flex flex-col border-l border-border/70 pl-1">
            {trace.events.map((e, i) => (
              <EventRow key={i} event={e} />
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
