"use client";

import { useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import Composer from "./components/Composer";
import { Label, Notice } from "./components/Notice";
import Markdown from "./components/Markdown";
import { TraceTrigger } from "./components/TraceDrawer";
import { API_BASE, readSSE, type Trace } from "./lib/api";

type Usage = { inputTokens: number; outputTokens: number };

type Message = {
  role: "user" | "assistant";
  content: string;
  // All filled in from SSE events that arrive after the last token.
  usage?: Usage;
  finishReason?: string;
  trace?: Trace;
};

/** Starters for an empty chat. Deliberately not about this repository: /chat
 *  has no retrieval, so a question about the project would be answered from
 *  whatever the model invents. Asking one of these and the same question on
 *  "Ask the docs" is the clearest way to see what retrieval is doing. */
const STARTERS = [
  "Explain what an embedding is, briefly.",
  "What is the difference between a prompt and a system message?",
  "Why do language models hallucinate?",
  "Write one sentence about the sea.",
];

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  async function send(e: React.FormEvent) {
    e.preventDefault();

    const text = input.trim();
    if (!text || sending) return;

    // the history the server sees, without the empty assistant placeholder
    const history: Message[] = [...messages, { role: "user", content: text }];

    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setSending(true);

    // every SSE event lands on the assistant message currently streaming
    const patchAssistant = (fn: (m: Message) => Message) =>
      setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        // the server's schema only has role and content; the display state
        // below it must not be sent back as history
        body: JSON.stringify({
          messages: history.map(({ role, content }) => ({ role, content })),
        }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`request failed: ${res.status}`);
      }

      await readSSE(res, (event, data) => {
        const payload = data as Record<string, never>;
        if (event === "token") {
          patchAssistant((m) => ({ ...m, content: m.content + payload.text }));
        } else if (event === "usage") {
          patchAssistant((m) => ({
            ...m,
            usage: {
              inputTokens: payload.input_tokens,
              outputTokens: payload.output_tokens,
            },
          }));
        } else if (event === "finish") {
          patchAssistant((m) => ({ ...m, finishReason: payload.reason }));
        } else if (event === "trace") {
          patchAssistant((m) => ({ ...m, trace: data as Trace }));
        } else if (event === "done") {
          return true; // stop reading
        }
      });
    } catch (err) {
      patchAssistant((m) => ({
        ...m,
        content: `${m.content}\n\n[error: ${
          err instanceof Error ? err.message : String(err)
        }]`,
      }));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Elements' Conversation: sticks to the bottom as tokens arrive and
          offers a scroll-back button once you leave it. */}
      <Conversation className="flex-1">
        <ConversationContent className="mx-auto w-full max-w-3xl px-5 pb-48 pt-8">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center gap-6 py-20">
              <ConversationEmptyState
                title="Nothing asked yet"
                description="Answers come from the model alone — it knows nothing about this repository. Open the trace under any reply to see what was sent and where the time went."
              />
              {/* Clicking fills the composer rather than sending, matching the
                  ask page and leaving the question editable first. */}
              <div className="flex flex-wrap justify-center gap-2">
                {STARTERS.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => setInput(q)}
                    className="rounded-full border border-border bg-card/60 px-3.5 py-1.5 text-xs text-muted-foreground transition-all hover:border-primary/50 hover:text-foreground active:scale-95"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-7">
              {messages.map((m, i) => {
                const streaming = sending && i === messages.length - 1;
                return (
                  <div key={i} className="flex flex-col gap-2">
                    <Label
                      className={m.role === "user" ? "text-primary/80" : undefined}
                    >
                      {m.role}
                    </Label>

                    {m.role === "user" ? (
                      // What the user typed, echoed. Not rendered: it is a
                      // record of input, not prose written to be read.
                      <p className="whitespace-pre-wrap leading-7 text-foreground/90">
                        {m.content}
                      </p>
                    ) : (
                      <div className="text-foreground">
                        <Markdown>{m.content}</Markdown>
                        {m.finishReason === "length" && (
                          <span className="text-warn">…</span>
                        )}
                        {/* The caret is the only motion during streaming: it
                            marks that tokens are still arriving. */}
                        {streaming && (
                          <span className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[2px] animate-pulse bg-primary align-baseline" />
                        )}
                      </div>
                    )}

                    {m.finishReason === "length" && (
                      <Notice tone="warn" title="Cut off.">
                        The model hit its output token limit, so this reply is
                        incomplete.
                      </Notice>
                    )}

                    {/* Token counts and the trace control belong to the same
                        fact about the call, so they sit on one row. */}
                    {(m.usage || m.trace) && (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                        {m.trace && (
                          <TraceTrigger
                            id={`chat-${i}`}
                            label={`chat · message ${Math.ceil((i + 1) / 2)}`}
                            trace={m.trace}
                          />
                        )}
                        {m.usage && (
                          <span className="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                            <span>in {m.usage.inputTokens}</span>
                            <span className="text-border">·</span>
                            <span>out {m.usage.outputTokens}</span>
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </ConversationContent>
        <ConversationScrollButton className="bottom-40" />
      </Conversation>

      <Composer
        value={input}
        onChange={setInput}
        onSubmit={send}
        busy={sending}
        placeholder="Ask the model something…"
        hint="Enter to send · Shift+Enter for a new line"
      />
    </div>
  );
}
