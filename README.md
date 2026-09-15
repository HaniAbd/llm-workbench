# llm-workbench

A workbench for learning to build with LLMs — streaming, structured output, prompt management, tracing and evaluation — against a model running on your own machine.

Everything talks to [Ollama](https://ollama.com) through its OpenAI-compatible endpoint, so there is no API key, no bill, and no network dependency. Swapping to a hosted provider is three environment variables.

It is built in steps, so expect finished parts next to unstarted ones. What works today:

- **Streaming chat** — FastAPI to browser over Server-Sent Events, with token counts, truncation detection and time-to-first-token.
- **Ticket classification** — a non-streaming endpoint returning a validated object, using Ollama's schema-constrained decoding rather than hoping the model returns valid JSON.
- **Prompts as files**, identified by a hash of their content, so a result can always be traced to the exact prompt text that produced it.
- **A trace panel** in the browser showing what was sent, what came back before anything reformatted it, and where the time went.
- **Retrieval over the repo's own docs** — a `/ask` endpoint answering from this repository's markdown, indexed in Postgres with pgvector, citing the documents it used.
- **An eval suite** that scores the classifier rather than pass/failing it, and compares a run against a reference you pin.
- **CI** covering everything that can be checked without a model.

## Getting started

**Prerequisites:** [Ollama](https://ollama.com) running, Python 3.14, Node 22.

```bash
ollama serve                 # usually already running via Ollama.app
ollama pull llama3.2

git clone https://github.com/HaniAbd/llm-workbench.git
cd llm-workbench
cp .env.example .env                 # scripts/ + api/
cp web/.env.example web/.env.local   # web/  (see note below)
```

Two env files, deliberately: Next.js only reads `.env*` from its own project directory and will not see the repo-root one. `web/` also works with no `.env.local` at all — it falls back to `http://localhost:8000`.

Then run the two halves in separate terminals:

```bash
# terminal 1 — the API
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload            # http://127.0.0.1:8000

# terminal 2 — the web app
cd web
npm install
npm run dev                          # http://localhost:3000
```

If port 3000 is taken, Next picks the next free one and prints it. That is fine — the API accepts any `localhost` port, so the browser will not block it.

Open the web app for a chat page and a `/classify` page, each with a trace panel. Or skip the browser:

```bash
curl -N http://localhost:8000/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"Name one sea."}]}'

curl -s http://localhost:8000/classify -H 'content-type: application/json' \
  -d '{"text":"I was charged twice this month. Please refund the duplicate."}'
```

> **Run the API from `api/`.** It loads `../.env` by relative path, so starting `uvicorn` from the repo root dies at import with `KeyError: 'OPENAI_BASE_URL'` — an error that names a missing variable when the cause is the working directory.

## Layout

| Directory | Stack | What it is |
| --- | --- | --- |
| [`api/`](api/) | FastAPI, `openai` SDK | The service. `POST /chat` streams SSE; `POST /classify` returns a schema-constrained object; `POST /agent` runs a tool-calling loop over the rest. Also the prompt store, tracing seam, eval suite and tests. **[Full documentation](api/README.md)** |
| [`web/`](web/) | Next.js 16, React 19, Tailwind v4 | Chat page, classifier page, and a trace panel for any call |
| [`scripts/`](scripts/) | Node, Vercel AI SDK | Numbered standalone experiments, read as much as run: first call, temperature, roles, streaming, error handling |
| [`evals/`](api/evals/) | — | 34 scored cases, run history, pinned reference |
| [`docker-compose.yml`](docker-compose.yml) | Postgres 17 + pgvector | The document index, on port 5433 |

The three parts are independent — no shared package, no build step linking them. The only contracts between them are the root `.env` and the SSE event protocol.

## The interesting parts

**Structured output is enforced, not requested.** `llama3.2` will happily return prose or an invented category. `response_format={"type": "json_schema", ...}` makes Ollama constrain decoding to a grammar, so out-of-enum values are unproducible — it holds even under prompt injection. Plain JSON mode is not enough: it returned `"category": "Billing Issue"` for the same ticket.

**Prompts live in files and are content-addressed.** `prompts/classify_ticket.md` is identified as `classify_ticket@99c7aa861057`, the digest being a hash of the rendered text. Editing a prompt changes its id automatically — the identity cannot silently keep meaning something new. The id reaches the response, the trace panel and the log line.

**Evaluation scores rather than passes.** Fields are scored two ways, because they are not the same kind of question: `category` is nominal and scored exactly, while `urgency` and `sentiment` are ordinal and give partial credit one step outside the accepted set. Expectations are always a *set* of acceptable values, never a single right answer.

```bash
cd api && python evals/run.py        # 34 cases, ~55s, needs Ollama
```

Runs append to `evals/runs.jsonl` and are compared against a reference you pin, so restoring a known-good prompt does not read as a regression.

## Asking about the repo

`/ask` answers from this repository's own markdown rather than from whatever the model happens to know. It needs two extras:

```bash
docker compose up -d                 # Postgres + pgvector on :5433
ollama pull nomic-embed-text         # the embedding model
cd api && python index_docs.py       # index the docs
```

Then ask, in the browser at `/ask` or directly:

```bash
curl -s http://localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"Why is the eval suite not run in CI?"}'
```

Every answer carries the passages behind it, with their similarity scores, precise enough to go and check — `api/README.md > api > CI > What CI cannot cover`. Indexing is a separate command, so re-indexing after editing a document takes effect on the next question with no restart.

It is deliberately the naive version — vector similarity, a fixed four passages, no keyword search or reranking — so the failure modes stay visible. Ask it "Name one sea" and it still retrieves four passages, none relevant.

## Letting the model choose

`/agent` gives the model the capabilities the API already has — answering from the docs, and classifying a ticket — and lets it decide which to use and in what order. The loop is hand-written, with no orchestration framework, so that a later step can rebuild it on one and the two can be compared.

```bash
curl -s http://localhost:8000/agent -H 'content-type: application/json' \
  -d '{"question":"Classify: \"I was charged twice.\" Then say what the docs cover."}'
```

Most of the loop is what happens when things go wrong. It is bounded three ways — steps, wall clock, and repeating an identical call — and **hitting a bound is a reported `stop_reason`, not an exception or a silent stop**. A tool that fails, or finds nothing, comes back as a result the model is expected to read and act on rather than something that kills the run; retrieval's existing `NOT_IN_DOCS` signal is reused rather than duplicated. Arguments that make no sense are rejected and reported, never repaired.

`llama3.2` handles this badly, and the [measured failures](api/README.md#what-the-model-actually-does) are the point: it refuses unanswerable questions well (7/7), but drops half of a two-part request 3 times in 5 — and then fabricates the half it skipped.

## Tests

```bash
cd api
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                     # 167 checks, under a second, no model needed
```

These run in CI on every push and pull request, alongside the `web` job: route-type generation, typecheck, lint and build. They deliberately cover only what works **without a model**: unrenderable prompts, `$ref` creeping into the JSON schema, scorer scales drifting from the schema enums, typo'd eval expectations, a renamed trace key that would break the web panel.

**The eval suite is not in CI, on purpose.** It needs a model, and a GitHub runner has neither one nor a route to yours. So CI answers *"is it wired up correctly"*, never *"is it any good"* — a prompt change that halves classification accuracy passes cleanly. Run the eval before trusting a prompt change. [What it would take to move it into CI](api/README.md#what-ci-cannot-cover).

## Further reading

- [`api/README.md`](api/README.md) — the service in detail: endpoints, the SSE protocol, structured output, prompts, traces, evaluation, CI
- [`CLAUDE.md`](CLAUDE.md) — orientation for AI coding agents working in this repo
