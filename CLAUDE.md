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
| [api/](api/) | FastAPI + `openai` Python SDK | `POST /chat` streams SSE; `POST /classify` returns a schema-constrained object; `POST /ask` answers from the repo's own docs; `POST /agent` runs a gated tool-calling loop |
| [web/](web/) | Next.js 16, React 19, Tailwind v4 | `/` chat, `/classify` classifier, `/ask` doc search, all with a trace panel |

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

#### Retrieval (`/ask`)

Answers questions from the repo's own markdown. Needs two things the rest does not: **Postgres with pgvector** (`docker compose up -d`, port 5433) and an **embedding model** (`ollama pull nomic-embed-text`, 768-dim). `llama3.2` can embed but at 3072 dims exceeds pgvector's 2000-dim index limit and is not trained for similarity.

```bash
cd api && python index_docs.py     # 51 chunks from 6 documents
```

Indexing is a **separate operation**; the API reads the index per request, so re-indexing needs no restart. Chunks follow markdown heading structure, and the heading path (`api/README.md > CI > What CI cannot cover`) travels with the chunk — it is prepended before embedding *and* is what makes a passage citable. `api/prompts/` is excluded by prefix so the model cannot retrieve its own instructions.

Selection is a candidate pool of 20 by cosine similarity, then the top three plus **one slot reserved for a document not already represented** — plain top-k is source-blind and one document would fill every slot on a question whose answer spans two (retrieval 0.619 → 0.691). A flat per-source cap, hybrid lexical+vector (RRF), and k=6 were all measured and reverted; see `api/README.md` for the numbers. Still no reranking, no keyword search.

`/ask` returns **`answered: bool`** — false means the docs do not contain the answer, a correct outcome rather than an error (errors are 502/503 with no answer). Produced by a similarity floor at 0.52, then a `NOT_IN_DOCS` sentinel the prompt asks for and the server strips. **A threshold alone does not work here**: AUC 0.829, the overlap band holds 8 of 21 answerable and 8 of 10 unanswerable, and the prompt alone already refuses 9/10 — better than any threshold's optimum. The floor works only because it sits below the lowest answerable score, a margin of 0.022 on ten off-topic questions. The sentinel costs `direct` 0.938 → 0.750; a JSON-constrained reply and a separate judge call were both measured and worse. `/chat` is untouched as a baseline. Retrieved passages and their scores appear in the trace panel. **`sources` in the response is authoritative** — the model's inline citations are prose and llama3.2 sometimes invents a heading path by stitching two together.

#### Structured output

`POST /classify` is the non-streaming counterpart to `/chat`. The finding that shapes it: **Ollama supports schema-constrained decoding through the OpenAI-compatible endpoint** (`response_format={"type": "json_schema", ...}`), so no provider-specific code path is needed. It is a decoding grammar — out-of-enum values are unproducible, which holds even under prompt injection. Plain `json_object` mode is *not* sufficient: it returned an invented `"Billing Issue"` category for the same ticket.

One pydantic model (`Classification`) is both the schema sent to the provider and the validator for the reply, so they cannot drift. Replies are re-validated on arrival and rejected rather than repaired.

Blank input is a 422 before any model call; junk gets a 200 with `is_support_ticket: false` and its other fields normalised to constants — **check the flag, not the category**, since a genuine ticket can also be `category: "other"`.

#### Evaluation

`api/evals/` scores the classifier rather than pass/failing it — `python evals/run.py` (34 cases, ~55s) prints one score plus a per-field and per-group breakdown, and appends to `evals/runs.jsonl`.

Two kinds of field, deliberately: `category` and the booleans are **exact** (nominal — a distance between "billing" and "technical" would be invented), while `urgency` and `sentiment` are **ordinal** and give 0.5 for one step outside the accepted set. Expectations are always a *set* of acceptable values, never one right answer; a field omitted from a case is not scored.

Comparability is enforced by digests: `scorer_digest` hashes the scoring rules, so changing how a score is computed **refuses** comparison with older runs rather than silently redefining the number. A changed `dataset_digest` still compares, on the shared cases. A changed `prompt_id` compares and is labelled `ACROSS PROMPTS` — that is the comparison the suite exists for.

Every run is compared twice, labelled and with run ids: **vs REFERENCE** (a run pinned in `evals/reference.json` via `--set-baseline`, which stays put until changed) and **vs previous run** (whatever ran last). **Regressions are judged against the reference** — comparing against the last run means each experiment is judged against the previous experiment, so restoring a known-good prompt reports phantom regressions. Unpinned, it falls back to previous-run behaviour and says so.

A `--group` run is a different measurement, not a smaller one: it is never saved or pinnable, its score is marked as not comparable to a full run, unexercised fields and unrun groups are named, and every failure count is scoped to the group — `0 regressions` over five cases is not an all-clear. Comparisons still work (the other run is recomputed over the same cases) and its full score is printed beside the recomputed one.

Failures split into **accepted** (blessed in `cases.json`), **regressions** (worse than baseline — exits non-zero), **outstanding** (known bad, no worse) and **fixed**, so a new break is never buried under a familiar one.

#### Retrieval evaluation

`python evals/run_retrieval.py` (31 cases, ~4 min) scores `/ask`. **Two scores, never blended** — `retrieval` (recall of expected passages, order ignored, fractional for multi-document answers) and `answer` (required facts; binary refusal for unanswerable cases). A third derived figure, `answer|found`, gives the answer score over cases where retrieval found everything: currently **0.923 against retrieval 0.691**, so the weakness remains the index rather than the model.

Four groups: `direct`, `vocabulary` (question words absent from the text), `multi_doc` (answer spans two documents), `unanswerable` (must refuse). Expected passages are named by a distinctive substring rather than a heading path, so re-chunking does not break the set.

Forbidden-claim matching is word-boundary (`MIT` occurs inside "limit"), contractions are expanded before matching, and a forbidden entry must name a value the model could only have invented — never a word the question uses, since a correct refusal restates the question.

`evals/harness.py` is shared with the classifier suite: run records, reference pinning, comparison and the miss buckets. Each suite keeps its own history and reference.

A run is stored in two places. `*_runs.jsonl` holds scores, digests, configuration and per-case **metrics** — small, permanent, **tracked**. `*_runs_detail/` holds the bulky per-case detail for the most recent 5 runs only and is **gitignored**. The split works because a baseline is only ever read through its metrics and case ids; detail is read from the current run alone. Read a kept run's detail with `--detail [RUN_ID]`; an older one says so rather than failing silently.

The `/ask` prompt is rendered with its passages, so the API's `prompt_id` differs per question; the suite records the **template** digest (`…~template`) to attribute a run.

A retrieval run also records `retrieval_digest` (the knobs) and `index_digest` (the corpus searched), read from `GET /retrieval/config` on the **running server** rather than from disk, since a file can be ahead of a server that has not restarted. Both are **flagged, not refused**: `scorer_digest` refuses because a changed scorer means the numbers are a different kind of measurement, whereas retrieval configuration is the thing *being* measured — comparing `k=4` with `k=6` is the point. They flag separately because a re-index moves one without the other. A run predating this reads as `not recorded`, never as "the same".

Knobs are collected by introspection over module constants in `answering` and `embeddings`, so adding one needs no edit — a hand-maintained list is exactly the gap that looks covered.

#### The similarity floor

One definition: `SIMILARITY_FLOOR` in `api/answering.py`, published at `GET /retrieval/config`, read by the front end **at runtime**. It is not a response schema, so it stays out of the generated types — and baking it in at build time would be the same bug the split caused, just slower to surface.

The UI previously kept its own `WEAK_MATCH_BELOW = 0.55` against the API's `0.52` and so contradicted it in between (measured: a question answered on 0.528 that the UI called weak). With no floor readable, scores are shown **with no verdict** and the UI says so rather than falling back to a number.

Since the floor gates only the top passage, an answered result has cleared it by definition; the banner therefore reports how many *supporting* passages fell below it, not a second opinion on the answer.

#### Generated API types

`api/openapi.json` and `web/app/lib/api.generated.ts` are generated from the pydantic models and **committed** — never hand-edited. `web/app/lib/api.ts` only aliases them; it declares no shapes.

```bash
cd api && python dump_openapi.py && cd ../web && npm run gen:api
```

Each link is checked on the side that has the tooling: `pytest` fails if `openapi.json` is stale (`tests/test_api_contract.py`), `npm run check:api` fails if the TypeScript is. So a rename cannot pass on one side while silently breaking the other — which it used to, because the trace was written out five times over.

`TraceEvent` is `extra="allow"` → `additionalProperties: true` → a TS index signature, so a **new event kind costs no change anywhere**. A new top-level trace field is two adjacent edits in `tracing.py` plus the regenerate commands.

#### Development traces

Both endpoints return a `trace` beside their normal output — a field on `/classify`, a `trace` SSE event after the last token on `/chat` (so it cannot delay streaming). It is a **superset of the `llm_call` log line**: the log stays scalar and greppable, the trace adds `messages_sent`, `raw_output`, and an ordered `events` list.

`events` is the growth path — retrieval, tool calls and agent steps append there without changing any existing field, and `TracePanel` renders unknown kinds generically. `normalised_non_ticket` already shows what the model said before the server replaced it with constants.

#### The agent loop

`api/agent.py` is `/agent`: a hand-written tool-calling loop over `answering.answer_question` and `classification.classify`, both wrapped unchanged. **No orchestration framework, deliberately** — a later step rebuilds it on one and the comparison is the point.

It is bounded by `MAX_STEPS`, `TIME_BUDGET_S` and `MAX_REPEATED_CALLS`, and every bound is a **reported `stop_reason` on a 200**, never an exception or a silent stop. `StopReason` is a `Literal`, so the values reach the generated TypeScript; a test asserts the published enum matches the code.

Nothing the model does raises. An unknown tool, unparseable or wrongly-typed arguments, an undeclared key, a failing capability and an unreachable index all become tool results carrying a leading signal (`NO_SUCH_TOOL`, `BAD_ARGUMENTS`, `TOOL_FAILED`, `INDEX_UNAVAILABLE`) for the model to act on. `NOT_IN_DOCS` is `answering.REFUSAL_SENTINEL` reused, not a second name for it. Note an unreachable index is a 200 here while `/ask` returns 503; only the loop's own model call failing is a 502.

**Malformed arguments are rejected, never coerced**, even where the intent is obvious — a loop that patches the model's mistakes says nothing about the model. The measured failures are documented in `api/README.md` and should be updated, not tuned away: argument quality swings on one line of the prompt (1/9 malformed with it, 6/15 without), and two-part requests lose their second half 3 times in 5, with the skipped half fabricated.

One agent run writes **several** `llm_call` log lines — each tool that calls the model opens its own span — while the returned trace covers the whole run, with run-total token counts and one `tool_call` event per capability invoked.

#### Human approval

`reindex_document` is the one tool that **changes** anything (it re-runs `index_docs.index_document` for one file) and the only gated one. Chosen because it is reversible by re-running, needs no external service or credentials, and is the one genuine write this codebase already performs.

**`requires_approval` is a field on the server's tool table and nothing else.** It is not in the JSON Schema the model sees and no argument reaches it — a model that can mark its own action safe has not been gated. Arguments are validated *before* the gate (`ReindexArgs` resolves its path against `index_docs.documents()`), so nobody is ever asked to approve a path that does not exist and traversal is a `BAD_ARGUMENTS` reply.

Three answers: **approved** runs the tool; **rejected** becomes an `ACTION_REJECTED` tool result carrying the reason, which the model is expected to accept and continue past (the run still ends `answered`); **expired** after `APPROVAL_TIMEOUT_S` (300s) is terminal — `stop_reason: "approval_expired"`, recorded as a step, and a later decision gets a `409`. A refusal is remembered against its validated arguments, so the model cannot put the same question to a person twice. **Time spent paused is added back to the deadline** — `TIME_BUDGET_S` measures the agent working, not a person thinking.

`api/runs.py` makes a paused run addressable from outside the process, which is the whole point: a front end cannot approve what it cannot see. `POST /agent` returns `202` + `run_id`; `GET /agent/{id}?wait=` blocks (≤25s) until the run needs you or finishes; `POST /agent/{id}/decision` releases it; `GET /agent/runs` is the queue. Runs live in a **dict in memory** — restart and pending approvals are gone, which is right for a workbench and wrong for anything else. There is **no authentication**: "a person decided" means "someone on localhost decided".

Measured: with the shipped prompt the gate fires only on explicit re-index requests, but an **earlier prompt revision had the model propose a write while answering a pure lookup**. Whether it does that is prompt-sensitive, which is exactly why the gate is not prompt-based. It also consistently fails the other way — it ignores a re-index request naming `CLAUDE.md` and always names `api/README.md` whatever the question says.

Transport is **inline, deliberately**: a caller only ever sees its own call, so there is no trace store to query and no id to guess. The trade is that every response carries the full rendered prompt — fine locally, the first thing to revisit if this leaves localhost. A `502` carries a trace; a `422` does not, because nothing was called.

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

## CI and tests

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on push and pull request: `api` (pytest) and `web` (tsc, lint, build).

```bash
cd api && pip install -r requirements.txt -r requirements-dev.txt && python -m pytest
```

`api/tests/` holds 167 **model-free** checks (<1s) chosen because they fail *silently* today: unrenderable prompts, `$ref` creeping into the JSON schema, scorer scales drifting from the schema enums, typo'd dataset expectations that score zero forever, a renamed trace key that breaks the web panel. `tests/conftest.py` supplies fake `OPENAI_*` values because CI has no `.env` and `main.py` reads them at import.

**The eval suite is deliberately not in CI** — it needs llama3.2 on your machine, and a runner has neither the model nor a route to yours. CI answers "is it wired up correctly", never "is it any good": a prompt change that halves accuracy passes cleanly. Run `python evals/run.py` before trusting one. See "What CI cannot cover" in [api/README.md](api/README.md) for what moving it would take.

`scripts/` and `web/` have no tests of their own beyond the build (`scripts`' `npm test` is the npm-init placeholder).
