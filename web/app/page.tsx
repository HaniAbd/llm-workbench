"use client";

import { useState } from "react";

type Usage = { inputTokens: number; outputTokens: number };

type Message = {
  role: "user" | "assistant";
  content: string;
  // Both are filled in from SSE events that arrive after the last token.
  usage?: Usage;
  finishReason?: string;
};

const API_URL = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/chat`;

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
      setMessages((prev) =>
        prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)),
      );

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "content-type": "application/json" },
        // the server's schema only has role and content; usage/finishReason
        // are local display state and must not be sent back as history
        body: JSON.stringify({
          messages: history.map(({ role, content }) => ({ role, content })),
        }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`request failed: ${res.status}`);
      }

      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";

      // SSE frames are separated by a blank line; a frame may be split across
      // reads, so only parse what is terminated.
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += value;

        let split: number;
        while ((split = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);

          let event = "message";
          let data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7);
            else if (line.startsWith("data: ")) data += line.slice(6);
          }
          if (!data) continue;

          const payload = JSON.parse(data);
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
          } else if (event === "done") {
            break outer;
          }
        }
      }

      await reader.cancel();
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
    <div className="flex flex-1 flex-col items-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-12">
        <h1 className="text-2xl font-semibold tracking-tight text-black dark:text-zinc-50">
          Chat
        </h1>

        <div className="flex flex-1 flex-col gap-4">
          {messages.length === 0 && (
            <p className="text-zinc-500 dark:text-zinc-400">
              Ask something to get started.
            </p>
          )}

          {messages.map((m, i) => (
            <div key={i} className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {m.role}
              </span>
              <p className="whitespace-pre-wrap leading-7 text-black dark:text-zinc-100">
                {m.content}
                {m.finishReason === "length" && (
                  <span className="text-amber-700 dark:text-amber-500">…</span>
                )}
                {sending && i === messages.length - 1 && (
                  <span className="animate-pulse">▌</span>
                )}
              </p>

              {m.finishReason === "length" && (
                <p className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-800 dark:text-amber-400">
                  Cut off — the model hit its output token limit. This reply is
                  incomplete.
                </p>
              )}

              {m.usage && (
                <p className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                  input {m.usage.inputTokens} · output {m.usage.outputTokens}
                </p>
              )}
            </div>
          ))}
        </div>

        <form onSubmit={send} className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Say something…"
            disabled={sending}
            className="flex-1 rounded-full border border-black/[.08] bg-white px-5 py-3 text-black outline-none placeholder:text-zinc-400 focus:border-black/[.3] disabled:opacity-50 dark:border-white/[.145] dark:bg-black dark:text-zinc-50 dark:focus:border-white/[.4]"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="rounded-full bg-foreground px-6 py-3 font-medium text-background transition-colors hover:bg-[#383838] disabled:opacity-40 dark:hover:bg-[#ccc]"
          >
            {sending ? "…" : "Send"}
          </button>
        </form>
      </main>
    </div>
  );
}
