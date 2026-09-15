"use client";

import { useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { cn } from "@/lib/utils";
import Composer from "./components/Composer";
import { Label, Notice } from "./components/Notice";
import TracePanel from "./components/TracePanel";
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
            <ConversationEmptyState
              className="py-24"
              title="Nothing asked yet"
              description="Answers come from the model alone. Open the trace under any reply to see what was sent and where the time went."
            />
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

                    <p
                      className={cn(
                        "whitespace-pre-wrap leading-7",
                        m.role === "user"
                          ? "text-foreground/90"
                          : "text-foreground",
                      )}
                    >
                      {m.content}
                      {m.finishReason === "length" && (
                        <span className="text-warn">…</span>
                      )}
                      {/* The caret is the only motion during streaming: it
                          marks that tokens are still arriving. */}
                      {streaming && (
                        <span className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[2px] animate-pulse bg-primary align-baseline" />
                      )}
                    </p>

                    {m.finishReason === "length" && (
                      <Notice tone="warn" title="Cut off.">
                        The model hit its output token limit, so this reply is
                        incomplete.
                      </Notice>
                    )}

                    {m.usage && (
                      <div className="flex gap-4 font-mono text-[11px] text-muted-foreground">
                        <span>in {m.usage.inputTokens}</span>
                        <span>out {m.usage.outputTokens}</span>
                      </div>
                    )}

                    {m.trace && <TracePanel trace={m.trace} />}
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
