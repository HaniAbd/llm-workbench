export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** One step in a call. `kind` is open-ended on purpose: retrieval, tool calls
 *  and agent steps will arrive as new kinds, and the panel renders any of them
 *  without needing to know them in advance. */
export type TraceEvent = {
  at_ms: number;
  kind: string;
  [key: string]: unknown;
};

export type SentMessage = { role: string; content: string };

/** One passage retrieved from the document index, with how well it matched. */
export type Retrieved = {
  source: string;
  heading_path: string;
  text: string;
  score: number;
};

/** A source behind an answer. Same shape minus the passage body. */
export type Source = { source: string; heading_path: string; score: number };

export type AskResult = {
  answer: string;
  sources: Source[];
  prompt_id: string;
  trace: Trace;
};

export type Trace = {
  model: string;
  prompt_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  ttft_ms: number | null;
  latency_ms: number;
  finish_reason: string | null;
  error: string | null;
  messages_sent: SentMessage[] | null;
  retrieved: Retrieved[] | null;
  raw_output: string | null;
  events: TraceEvent[];
};

export type Classification = {
  is_support_ticket: boolean;
  category: string;
  urgency: string;
  sentiment: string;
  requires_human: boolean;
  prompt_id: string;
  trace: Trace;
};

/** Reads an SSE body, handing each complete frame to `onEvent`.
 *  Frames are split across reads, so only what is terminated by a blank line
 *  is parsed. */
export async function readSSE(
  res: Response,
  onEvent: (event: string, data: unknown) => boolean | void,
) {
  if (!res.body) throw new Error("response has no body");
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
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
        if (onEvent(event, JSON.parse(data)) === true) break outer;
      }
    }
  } finally {
    await reader.cancel();
  }
}
