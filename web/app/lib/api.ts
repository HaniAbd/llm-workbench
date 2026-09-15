import type { components } from "./api.generated";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Every shape below is an alias onto `api.generated.ts`, which is generated
 *  from `api/openapi.json`, which FastAPI derives from its pydantic models.
 *  There is no hand-written copy of any of these: a field renamed in
 *  `api/tracing.py` fails `npm run check:api` rather than silently leaving the
 *  panel a field short.
 *
 *  Regenerate with:  cd api && python dump_openapi.py && cd ../web && npm run gen:api
 *
 *  `Required<...>` because pydantic marks a field with a default as optional
 *  in the schema, while the server always serialises every key. The names on
 *  the left are the app's; the names on the right are the API's. */
type Schemas = components["schemas"];

export type Trace = Required<Schemas["TraceDocument"]>;
export type TraceEvent = Schemas["TraceEvent"];
export type Retrieved = Schemas["RetrievedPassage"];
export type SentMessage = Schemas["SentMessage"];
export type Source = Schemas["Source"];
export type SourceDocument = Required<Schemas["Document"]>;
// `Required` is shallow, so the nested trace is restated to pick up the same
// treatment rather than arriving half-optional.
export type AskResult = Required<Omit<Schemas["AskResponse"], "trace">> & {
  trace: Trace;
};
export type Classification = Required<
  Omit<Schemas["ClassificationResponse"], "trace">
> & { trace: Trace };

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
