"use client";

import { ArrowUp } from "lucide-react";
import { useEffect, useRef, type FormEvent } from "react";
import { Loader } from "@/components/ai-elements/loader";
import { cn } from "@/lib/utils";

/** The composer, pinned to the bottom of the viewport.
 *
 *  Not Elements' PromptInput: that component's value is attachments, model
 *  selectors and action menus, none of which this app has, and adopting it
 *  would have pulled six shadcn components and cmdk in to render a textarea
 *  and a button. What it does provide and this borrows is the idea of a
 *  submit control that carries the request's state rather than sitting inert
 *  beside it.
 *
 *  Fixed rather than sticky so the conversation scrolls behind it; the page
 *  pads its own bottom by the composer's height. */
export default function Composer({
  value,
  onChange,
  onSubmit,
  busy,
  placeholder,
  hint,
  allowEmpty = false,
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  placeholder: string;
  hint?: string;
  /** Let blank input be submitted. Off for chat and ask, where an empty send
   *  is only ever a mistake; on for classify, where submitting whitespace is
   *  how the 422 rejection is demonstrated and the endpoint's own validation
   *  is the thing being shown. */
  allowEmpty?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the text, to a ceiling, then scroll inside.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-20">
      {/* The conversation fades out behind the composer rather than being
          clipped by a hard edge. */}
      <div className="h-16 bg-gradient-to-b from-transparent to-background" />
      <div className="bg-background pb-5">
        <form
          onSubmit={onSubmit}
          className="pointer-events-auto mx-auto w-full max-w-3xl px-5"
        >
          <div
            className={cn(
              "flex items-end gap-2 rounded-2xl border bg-card p-2 pl-4 shadow-lg shadow-black/25 transition-colors duration-300",
              busy ? "border-primary/50" : "border-border focus-within:border-primary/60",
            )}
          >
            <textarea
              ref={ref}
              rows={1}
              value={value}
              disabled={busy}
              placeholder={placeholder}
              onChange={(e) => onChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  onSubmit(e);
                }
              }}
              className="flex-1 resize-none self-center bg-transparent py-2 text-sm leading-6 text-foreground outline-none placeholder:text-muted-foreground/70 disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={busy || (!allowEmpty && !value.trim())}
              aria-label={busy ? "Waiting for the model" : "Send"}
              className={cn(
                "grid size-9 shrink-0 place-items-center rounded-xl transition-all duration-200",
                "bg-primary text-primary-foreground",
                "enabled:hover:brightness-110 enabled:active:scale-95",
                "disabled:bg-muted disabled:text-muted-foreground",
              )}
            >
              {busy ? <Loader size={16} /> : <ArrowUp className="size-4" />}
            </button>
          </div>
          {hint && (
            <p className="px-1 pt-2 text-center text-[11px] text-muted-foreground/70">
              {hint}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
