"use client";

import { Check, Clock, ShieldAlert, X } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Loader } from "@/components/ai-elements/loader";
import { Label } from "./Notice";
import type { PendingAction } from "../lib/api";

/** The decision surface: what the agent wants to do, and the two answers.
 *
 *  Deliberately not a yes/no button. Approving is a decision, so the card
 *  carries what a decision needs - the exact tool, the exact arguments, and
 *  what the server says running it will change. `effect` is the load-bearing
 *  line: it is declared on the server's tool table, not written by the model,
 *  so it describes what the code will do rather than what the agent claims.
 *
 *  Rejecting wears a plain bordered button, not a red one. A refusal is an
 *  ordinary outcome that the run carries on past, and dressing it as
 *  destructive would teach the wrong thing about what the gate is for. */

/** Seconds left, anchored on arrival rather than on the server's clock.
 *
 *  The server sends both an absolute `expires_at` and a relative
 *  `expires_in_s`; the relative one is used because it cannot be wrong by the
 *  difference between two machines' clocks.
 *
 *  The deadline is fixed once, when the component mounts. Re-anchoring it on
 *  every render would let the countdown restart itself and never reach zero,
 *  so a *new* action gets a new countdown the way React intends - the page
 *  keys this component on `requested_at`, which remounts it. */
function useCountdown(seconds: number) {
  const [deadline] = useState(() => Date.now() + seconds * 1000);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const left = Math.max(0, Math.round((deadline - now) / 1000));
  return { left, total: seconds };
}

function Argument({ name, value }: { name: string; value: unknown }) {
  const rendered =
    typeof value === "string" ? value : JSON.stringify(value, null, 1);
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3">
      <span className="font-mono text-[11px] text-muted-foreground">{name}</span>
      <span className="break-words font-mono text-xs text-foreground">
        {rendered}
      </span>
    </div>
  );
}

export default function ApprovalCard({
  action,
  question,
  onDecide,
  deciding,
}: {
  action: PendingAction;
  /** What was asked. The origin of the proposal, and the only context the API
   *  exposes while a run is paused - see the page for why. */
  question: string;
  onDecide: (approved: boolean, reason: string) => void;
  deciding: boolean;
}) {
  const [reason, setReason] = useState("");
  const { left, total } = useCountdown(action.expires_in_s);
  const low = left <= 60;
  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, "0");

  return (
    <div
      className={cn(
        "animate-in fade-in slide-in-from-bottom-2 flex flex-col gap-4 rounded-xl border p-4 duration-500",
        "border-warn/40 bg-warn/[0.05] shadow-lg shadow-black/20",
      )}
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 animate-pulse text-warn" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-warn">
            The agent wants to do something. Decide before it runs.
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            It is paused. Nothing has changed yet.
          </p>
        </div>
        <span
          title={left === 0 ? "expired" : "time left to decide"}
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 font-mono text-[11px] tabular-nums transition-colors",
            low ? "bg-warn/20 text-warn" : "bg-muted text-muted-foreground",
          )}
        >
          <Clock className="size-3" />
          {minutes}:{seconds}
        </span>
      </div>

      {/* Drains rather than counting, so how much room is left reads without
          being converted from a number. */}
      <span className="h-0.5 w-full overflow-hidden rounded-full bg-muted">
        <span
          className={cn(
            "block h-full rounded-full transition-[width] duration-1000 ease-linear",
            low ? "bg-warn" : "bg-primary/70",
          )}
          style={{ width: `${total > 0 ? (left / total) * 100 : 0}%` }}
        />
      </span>

      <div className="flex flex-col gap-2">
        <Label className="text-warn/90">action</Label>
        <div className="rounded-lg bg-background/60 p-3 ring-1 ring-border/60">
          <div className="font-mono text-sm text-foreground">{action.tool}</div>
          <div className="mt-2 flex flex-col gap-1">
            {Object.entries(action.arguments).map(([name, value]) => (
              <Argument key={name} name={name} value={value} />
            ))}
            {Object.keys(action.arguments).length === 0 && (
              <span className="font-mono text-[11px] text-muted-foreground/60">
                no arguments
              </span>
            )}
          </div>
        </div>
      </div>

      {action.effect && (
        <div className="flex flex-col gap-2">
          <Label className="text-warn/90">what it changes</Label>
          <p className="text-sm leading-relaxed text-foreground/90">
            {action.effect}
          </p>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Label>asked</Label>
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          {question}
        </p>
      </div>

      <div className="flex flex-col gap-2.5 border-t border-border/60 pt-3.5">
        <input
          value={reason}
          disabled={deciding}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (optional) — the agent is told this"
          className="w-full rounded-lg border border-border bg-background/60 px-3 py-2 text-xs text-foreground outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary/60 disabled:opacity-60"
        />
        <div className="flex gap-2">
          <button
            type="button"
            disabled={deciding || left === 0}
            onClick={() => onDecide(true, reason)}
            className={cn(
              "flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-all",
              "bg-ok text-ok-foreground enabled:hover:brightness-110 enabled:active:scale-[0.98]",
              "disabled:bg-muted disabled:text-muted-foreground",
            )}
          >
            {deciding ? <Loader size={14} /> : <Check className="size-4" />}
            Approve
          </button>
          {/* Bordered, not red: refusing is a normal answer, not a destructive
              one, and the run continues afterwards. */}
          <button
            type="button"
            disabled={deciding || left === 0}
            onClick={() => onDecide(false, reason)}
            className={cn(
              "flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm transition-all",
              "border-border bg-card text-foreground",
              "enabled:hover:border-foreground/40 enabled:hover:bg-muted enabled:active:scale-[0.98]",
              "disabled:opacity-50",
            )}
          >
            <X className="size-4" />
            Reject
          </button>
        </div>
        <p className="text-center text-[11px] text-muted-foreground/70">
          {left === 0
            ? "Too late to decide — the run has stopped itself."
            : "Rejecting is not an error. The agent is told why and carries on."}
        </p>
      </div>
    </div>
  );
}
