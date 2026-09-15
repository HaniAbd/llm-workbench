"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, type SourceDocument } from "../lib/api";
import Markdown from "./Markdown";
import { Label, Notice } from "./Notice";

/** A source document, rendered, with the retrieved passage marked.
 *
 *  Rendered rather than shown as source, because a document is neither model
 *  prose nor a record of what the system did - it is the material itself,
 *  written for a person to read, and the question it answers is whether the
 *  answer was fair to it. You cannot judge that against raw markdown.
 *
 *  The passage is located by looking for its indexed text in the file. That is
 *  the only reliable key: heading paths are not unique within a document, and
 *  the chunk carries no offsets. When the file has changed since indexing the
 *  text simply is not there any more, which is an outcome to report, not an
 *  error - 4 of the 59 passages currently indexed are already in that state. */
export default function DocumentView({
  path,
  passage,
  headingPath,
}: {
  path: string;
  passage: string;
  headingPath: string;
}) {
  const [doc, setDoc] = useState<SourceDocument | null>(null);
  const [error, setError] = useState<string | null>(null);
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // No state reset here: the drawer keys this component by path, so a
    // different document arrives as a fresh mount rather than as stale state
    // being cleared.
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/documents/${path}`);
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(
            typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`,
          );
        }
        const body = (await res.json()) as SourceDocument;
        if (!cancelled) setDoc(body);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path]);

  const text = doc?.text ?? "";
  const start = text ? text.indexOf(passage) : -1;
  const found = start >= 0;

  // Bring the marked block into view once it has rendered. Scrolled rather
  // than jumped to, so it is clear the passage sits inside a larger document.
  useEffect(() => {
    if (!found) return;
    const marked = container.current?.querySelector("[data-passage]");
    marked?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [found, doc]);

  if (error) {
    return (
      <Notice tone="danger" title="Could not load the document.">
        {error}
      </Notice>
    );
  }
  if (!doc) {
    return (
      <p className="text-sm text-muted-foreground">
        <span className="mr-2 inline-block size-1.5 animate-pulse rounded-full bg-primary align-middle" />
        loading {path}…
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <Label>passage</Label>
        <p className="break-words font-mono text-[11px] text-muted-foreground">
          {headingPath}
        </p>
      </div>

      {!doc.on_disk && (
        <Notice tone="warn" title="This document is no longer on disk.">
          It is still in the index, so retrieval can still return passages from
          it, but there is nothing left to read it against.
        </Notice>
      )}

      {doc.on_disk && !found && (
        <Notice tone="warn" title="This passage is no longer in the document.">
          The file has changed since it was indexed
          {doc.indexed_at ? ` on ${doc.indexed_at.slice(0, 10)}` : ""}. The
          document below is the current file; the passage retrieval actually
          supplied is shown underneath it, unchanged.
        </Notice>
      )}

      {doc.on_disk && (
        <div ref={container}>
          <Markdown variant="document" highlight={found ? { start, end: start + passage.length } : null}>
            {text}
          </Markdown>
        </div>
      )}

      {!found && (
        <div className="flex flex-col gap-1">
          <Label>the passage as indexed</Label>
          {/* Literal: this is the record of what retrieval supplied, not the
              document. It keeps its own rules. */}
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-background/60 p-3 font-mono text-[11px] leading-relaxed text-foreground/80 ring-1 ring-border/60">
            {passage}
          </pre>
        </div>
      )}
    </div>
  );
}
