# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A learning workbench for building with LLMs (streaming, structured output, evals, RAG, agents, tracing). Work is added incrementally as numbered steps, so expect partially-built areas rather than a finished product. `web/` is untracked.

## Everything points at local Ollama

`scripts/` and `api/` talk to Ollama through its OpenAI-compatible endpoint, configured by the **repo-root `.env`** (see [.env.example](.env.example)):

```
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
MODEL=llama3.2
```

Both `scripts/` and `api/` load that file by the **relative path `../.env`**, so they only work when run with their own directory as cwd. Ollama must be running (`ollama serve`) or every call fails with a connection error.

`web/` does **not** share that file. Next.js only reads `.env*` from its own project directory, so the browser-side config lives in [web/.env.example](web/.env.example) and is a separate copy step:

```bash
cp .env.example .env                 # scripts/ + api/
cp web/.env.example web/.env.local   # web/
```

`NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) points the chat UI at the FastAPI service. It is inlined into the browser bundle at build time, so changing it requires a rebuild, not just a restart. The code falls back to the default when it is unset, so `web/` runs without any `.env.local` at all.

There is no real API key anywhere — swapping to a hosted provider is a matter of changing these three vars (and the `PRICING` table in [scripts/cost.js](scripts/cost.js), which holds per-1M-token prices and treats local models as free).

## The three parts

| Dir | Stack | Role |
| --- | --- | --- |
| [scripts/](scripts/) | Node ESM, Vercel AI SDK (`ai` + `@ai-sdk/openai`) | Numbered standalone experiments, run directly with `node` |
| [api/](api/) | FastAPI + `openai` Python SDK | `POST /chat` streams SSE; `POST /classify` returns a schema-constrained object |
| [web/](web/) | Next.js 16, React 19, Tailwind v4 | Chat UI — streams from the API and renders the transcript |

They are independent: no shared package, no build step linking them. The only contracts between them are the root `.env` and the SSE protocol below.

### scripts/

Each file is a self-contained demo of one concept and is meant to be read as much as run: `01-first-call` (text/usage/finishReason/cost), `02-temperature`, `03-roles` (system instructions + message history), `04-stream` (time-to-first-chunk), `05-errors` (truncation via `maxOutputTokens`, `abortSignal` timeouts, manual exponential-backoff retry with `maxRetries: 0` to disable the SDK's own).

```bash
cd scripts && npm install
node 01-first-call.js      # any single file; they are not wired to npm scripts
```

### api/

`api/main.py` holds the routes; `api/classification.py` is the `/classify` task (schema, provider call); `api/prompts/` is prompt text plus its loader; `api/tracing.py` is the observability seam. See [api/README.md](api/README.md) for the full service docs. `main.py` hand-rolls SSE (`sse()` helper) rather than using a library, emitting four event types that the web client must handle:

- `token` → `{text}` — one content delta
- `finish` → `{reason}` — the provider's finish_reason (`length` means truncated; don't parse that output)
- `usage` → `{input_tokens, output_tokens}` — arrives at the end, requires `stream_options={"include_usage": True}`
- `done` → `{}` — terminator

CORS accepts **any localhost port** via `allow_origin_regex`, not a fixed origin — the Next dev server falls back to 3001, 3002, … whenever its usual port is taken by another project, and a hardcoded origin breaks the page with an opaque "Failed to fetch" when it does. The system prompt and `temperature=0` are hardcoded server-side, and `Message.role` is `Literal["user", "assistant"]` so a client cannot supply a `system` turn of its own — such a request is **rejected with a 422** by validation, before the handler runs, rather than being stripped. Rejected requests never open a span, so they leave no `llm_call` line.

#### Structured output

`POST /classify` is the non-streaming counterpart to `/chat`. The finding that shapes it: **Ollama supports schema-constrained decoding through the OpenAI-compatible endpoint** (`response_format={"type": "json_schema", ...}`), so no provider-specific code path is needed. It is a decoding grammar — out-of-enum values are unproducible, which holds even under prompt injection. Plain `json_object` mode is *not* sufficient: it returned an invented `"Billing Issue"` category for the same ticket.

One pydantic model (`Classification`) is both the schema sent to the provider and the validator for the reply, so they cannot drift. Replies are re-validated on arrival and rejected rather than repaired.

Blank input is a 422 before any model call; junk gets a 200 with `is_support_ticket: false` and its other fields normalised to constants — **check the flag, not the category**, since a genuine ticket can also be `category: "other"`. `samples/run.py` exercises 18 hand-checkable tickets.

#### Prompt store

Prompt text lives in `api/prompts/*.md`, never inline in Python. **Editing a prompt needs no code change and no restart** — files are read per request, since `--reload` watches `.py` only.

Identity is `name@digest`, the digest being SHA-256 of the *rendered* text (`classify_ticket@70645fce0f63`). Because the id is derived from the text, editing a prompt cannot silently keep its old id — the guarantee is structural, not a convention. Old text lives in git history rather than on disk; that is the deliberate trade.

The id reaches the caller (`prompt_id` on `/classify`, a `prompt` SSE event on `/chat`) **and** the `llm_call` trace line, so a recorded result is attributable to an exact prompt.

Adding a prompt is adding a `.md` file and calling `prompts.get("name")` — no registry. Placeholders are `$name` (`string.Template`), not `{name}`, because these prompts discuss JSON. `classify_ticket.md` receives its enum values from `classification.py` this way, so prompt and schema cannot disagree.

#### Tracing seam

[api/tracing.py](api/tracing.py) emits **one JSON line per LLM call** on the `llm.trace` logger — model, token counts, `ttft_ms`, `latency_ms`, finish reason:

```json
{"event": "llm_call", "model": "llama3.2", "prompt_id": "classify_ticket@70645fce0f63", "input_tokens": 38, "output_tokens": 70, "ttft_ms": 100, "latency_ms": 2026, "finish_reason": "stop", "error": null}
```

`chat_span(model)` is a context manager yielding a mutable span; the handler marks `first_token()`, `set_usage()` and `finish_reason` as events arrive, and the line is written from a `finally` on close. It is shaped like an OTel span / Langfuse generation on purpose — **replacing it with a real tracing library means rewriting `chat_span` and `_emit`, not touching the handler.** `tracing.py` deliberately knows nothing about OpenAI or FastAPI.

Two non-obvious properties:

- It attaches its **own stdout handler** rather than inheriting one. uvicorn leaves the root logger bare, so a plain `getLogger(...).info(...)` is dropped silently; owning the handler is also what keeps each record a single `jq`-parseable line instead of one prefixed with `INFO:`.
- **Aborted calls are not logged.** Starlette iterates the sync generator in a threadpool and abandons it on client disconnect rather than closing it, so `finally` never runs. Completed and failed calls (the latter with `error` set) are logged; aborted ones are not.

```bash
cd api
source .venv/bin/activate          # Python 3.14
pip install -r requirements.txt
uvicorn main:app --reload          # http://127.0.0.1:8000
curl -N http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"hi"}]}'
```

### web/

```bash
cd web
npm run dev     # 3000, or the next free port; any localhost port is CORS-allowed
npm run build
npm run lint
```

[web/AGENTS.md](web/AGENTS.md) (linked from `web/CLAUDE.md`) is generated by `next dev` and applies here: **this is Next.js 16, which differs from older versions in APIs and conventions — consult `web/node_modules/next/dist/docs/` before writing Next code.** Note e.g. `layout.tsx` using the generated `LayoutProps<"/">` type instead of a hand-written props interface. Do not strip the generated block from `web/AGENTS.md`; `next dev` just re-adds it.

## No tests

There is no test framework in any sub-project (`scripts`' `npm test` is the npm-init placeholder). Verification is by running a script or curling the endpoint.
