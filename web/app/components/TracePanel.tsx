"use client";

import { ChevronRight, Clock, Coins, FileText, Sparkles, Zap } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { WEAK_MATCH_BELOW, type Trace, type TraceEvent } from "../lib/api";
import { Label } from "./Notice";


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

function Section({
  title,
  children,
  right,
}: {
  title: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <Label>{title}</Label>
        {right}
      </div>
      {children}
    </div>
  );
}

const Pre = ({ children }: { children: React.ReactNode }) => (
  <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-background/60 p-3 font-mono text-[11px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
    {children}
  </pre>
);

/** Renders any step. Fields beyond at_ms/kind are shown generically, so a
 *  future retrieval or tool-call step needs no change here. */
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

export default function TracePanel({ trace }: { trace: Trace }) {
  // Open by default. This panel is the reason the app exists; hiding it
  // behind a click made it something you had to remember to look at.
  const [open, setOpen] = useState(true);

  return (
    <div className="overflow-hidden rounded-xl border border-border/70 bg-card/70">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-muted/40"
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
            open && "rotate-90",
          )}
        />
        <span className="text-xs font-medium text-foreground">Trace</span>
        <span className="truncate font-mono text-[11px] text-muted-foreground">
          {trace.prompt_id ?? "no prompt"}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-3 font-mono text-[11px]">
          {trace.error ? (
            <span className="text-danger">{trace.error}</span>
          ) : (
            <span className="text-muted-foreground">{trace.latency_ms}ms</span>
          )}
        </span>
      </button>

      <div
        className={cn(
          "grid transition-all duration-300 ease-out",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="flex flex-col gap-5 border-t border-border/70 p-3.5">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
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
                title={`retrieved · ${trace.retrieved.length} passages, best first`}
              >
                <div className="flex flex-col gap-2.5">
                  {trace.retrieved.map((r, i) => {
                    const weak = r.score < WEAK_MATCH_BELOW;
                    return (
                      <div key={i} className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span
                            className={cn(
                              "w-12 shrink-0 font-mono text-xs",
                              weak ? "text-warn" : "text-ok",
                            )}
                          >
                            {r.score.toFixed(3)}
                          </span>
                          {/* The bar makes a weak match visible before the
                              number is read. */}
                          <span className="h-1 w-24 shrink-0 overflow-hidden rounded-full bg-muted">
                            <span
                              className={cn(
                                "block h-full rounded-full transition-[width] duration-500 ease-out",
                                weak ? "bg-warn" : "bg-ok",
                              )}
                              style={{
                                width: `${Math.max(0, Math.min(1, r.score)) * 100}%`,
                              }}
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
                <div className="flex flex-col gap-2">
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
        </div>
      </div>
    </div>
  );
}
