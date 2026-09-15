import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** The app's semantic vocabulary, in one place.
 *
 *  Every page makes the same four distinctions and they used to be spelled
 *  out in ad-hoc amber and red classes that had already drifted apart. Naming
 *  them here is what makes a weak match look like a weak match on whichever
 *  page you meet it.
 *
 *  `info` is deliberately separate from `danger`: the model declining to
 *  answer is a correct outcome, and it should not wear the colour of a broken
 *  request. */
export type NoticeTone = "info" | "warn" | "danger" | "ok";

const TONES: Record<NoticeTone, string> = {
  ok: "border-ok/35 bg-ok/10 text-ok",
  info: "border-info/35 bg-info/10 text-info",
  warn: "border-warn/35 bg-warn/10 text-warn",
  danger: "border-danger/40 bg-danger/10 text-danger",
};

export function Notice({
  tone,
  title,
  children,
  className,
}: {
  tone: NoticeTone;
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "animate-in fade-in slide-in-from-top-1 rounded-lg border px-3.5 py-2.5 text-sm duration-300",
        TONES[tone],
        className,
      )}
    >
      <strong className="font-medium">{title}</strong>{" "}
      <span className="text-foreground/75">{children}</span>
    </div>
  );
}

/** A small caps label. Used for every section heading in the app so the
 *  pages share a rhythm rather than each inventing one. */
export function Label({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}
