"use client";

import { Dialog } from "@base-ui/react/dialog";
import { Activity, X } from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Trace } from "../lib/api";
import DocumentView from "./DocumentView";
import TraceBody from "./TraceBody";

/** One drawer for the whole app, opened from anywhere.
 *
 *  The trace used to sit inline beneath each answer, which meant opening it
 *  reflowed the conversation and it fought the answer for the same column.
 *  Here it overlays from the right instead: the page behind does not move, and
 *  the trace gets its own full-height column to be read in.
 *
 *  Built on Base UI's Dialog, which is already a dependency through shadcn, so
 *  focus handling, Escape and scroll locking come from it rather than from me.
 *  shadcn's own sheet would have pulled in radix-ui, a second primitive
 *  library beside the one already here. */
/** The drawer shows one of two things. A trace is a record of a call; a
 *  document is the source behind one retrieved passage. They share the drawer
 *  because they answer the same question from opposite ends - what happened,
 *  and what it was drawn from. */
type Open =
  | { kind: "trace"; id: string; label: string; trace: Trace }
  | { kind: "document"; id: string; label: string; path: string; passage: string; headingPath: string }
  | null;

const TraceContext = createContext<{
  open: Open;
  setOpen: (next: Open) => void;
}>({ open: null, setOpen: () => {} });

export function TraceProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<Open>(null);
  return (
    <TraceContext.Provider value={{ open, setOpen }}>
      {children}
      <Dialog.Root
        open={open !== null}
        onOpenChange={(next) => !next && setOpen(null)}
      >
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-background/70 backdrop-blur-[2px] transition-opacity duration-300 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
          <Dialog.Popup
            className={cn(
              "fixed inset-y-0 right-0 z-50 flex w-full max-w-[38rem] flex-col",
              "border-l border-border bg-card shadow-2xl shadow-black/50",
              "transition-transform duration-300 ease-out",
              "data-[ending-style]:translate-x-full data-[starting-style]:translate-x-full",
            )}
          >
            <div className="flex items-start gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0 flex-1">
                <Dialog.Title className="text-sm font-medium text-foreground">
                  {open?.kind === "document" ? "Source document" : "Trace"}
                </Dialog.Title>
                {/* Which call this belongs to, spelled out rather than implied
                    by whatever happens to be behind the overlay. */}
                <Dialog.Description className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                  {open?.label}
                </Dialog.Description>
              </div>
              <Dialog.Close
                aria-label="Close trace"
                className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </Dialog.Close>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
              {open?.kind === "trace" && <TraceBody trace={open.trace} />}
              {open?.kind === "document" && (
                <DocumentView
                  key={`${open.path}:${open.id}`}
                  path={open.path}
                  passage={open.passage}
                  headingPath={open.headingPath}
                />
              )}
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </TraceContext.Provider>
  );
}

/** The plain text control that opens a trace.
 *
 *  `id` is what makes it obvious which call is open: the trigger for the
 *  trace currently on screen reads as active while every other one does not. */
export function TraceTrigger({
  id,
  label,
  trace,
}: {
  id: string;
  label: string;
  trace: Trace;
}) {
  const { open, setOpen } = useContext(TraceContext);
  const isOpen = open?.id === id;
  return (
    <button
      type="button"
      onClick={() => setOpen(isOpen ? null : { kind: "trace", id, label, trace })}
      aria-expanded={isOpen}
      className={cn(
        "group inline-flex w-fit items-center gap-2 rounded-lg border px-2.5 py-1.5",
        "text-xs transition-all duration-200 active:scale-[0.97]",
        isOpen
          ? "border-primary/50 bg-primary/10 text-primary"
          : "border-border/70 bg-card/60 text-muted-foreground hover:border-primary/40 hover:text-foreground",
      )}
    >
      {/* The icon pulses only while its drawer is open, so the motion marks a
          state rather than decorating a button that is doing nothing. */}
      <Activity
        className={cn(
          "size-3.5 shrink-0 transition-transform duration-300",
          isOpen ? "animate-pulse text-primary" : "group-hover:translate-x-0.5",
        )}
      />
      <span>{isOpen ? "trace open" : "trace"}</span>
      <span
        className={cn(
          "rounded-md px-1.5 py-0.5 font-mono text-[10px] tabular-nums transition-colors",
          trace.error
            ? "bg-danger/15 text-danger"
            : isOpen
              ? "bg-primary/15 text-primary"
              : "bg-muted text-muted-foreground group-hover:bg-muted/80",
        )}
      >
        {trace.error ? "error" : `${trace.latency_ms}ms`}
      </span>
    </button>
  );
}

/** Opens the source document behind one retrieved passage.
 *
 *  Presentation only: the sources list stays exactly what it was, the record
 *  of what retrieval supplied. Opening one adds a way to read around it and
 *  takes nothing away from that claim - which is why the row still shows its
 *  own score and heading path, and why the drawer labels what it shows as the
 *  source document rather than as the answer's evidence. */
export function useOpenDocument() {
  const { open, setOpen } = useContext(TraceContext);
  return {
    openDocument: (args: {
      id: string;
      label: string;
      path: string;
      passage: string;
      headingPath: string;
    }) => setOpen({ kind: "document", ...args }),
    openId: open?.kind === "document" ? open.id : null,
  };
}
