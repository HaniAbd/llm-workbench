# web

The browser front end for the FastAPI service in [`../api`](../api). Four pages, one per thing the API can do, each built to show *how* the answer was produced rather than only the answer.

Next.js 16 (App Router, Turbopack), React 19, Tailwind v4, shadcn/ui on Base UI, and two AI SDK Elements components. Dark only — the palette lives as tokens on `:root` in `app/globals.css` and there is no second one to keep in step.

## Running it

```bash
npm install
npm run dev      # 3000, or the next free port
```

The API must be running on `http://localhost:8000`. Its CORS rule accepts **any** localhost port, so the dev server falling back to 3001 does not break the page.

```bash
cp .env.example .env.local
```

`NEXT_PUBLIC_API_URL` points at the API and defaults to `http://localhost:8000`, so `web/` runs with no `.env.local` at all. Next reads `.env*` from this directory only — it does **not** read the repo-root `.env` that `scripts/` and `api/` use, which is why this is a separate copy step. `NEXT_PUBLIC_` values are inlined into the bundle at build time, so changing one needs a rebuild, not a restart.

```bash
npm run build
npm run lint
```

## The pages

| Route | What it is | What it shows |
| --- | --- | --- |
| `/` | Chat | Streams tokens over SSE, and reports token usage and truncation rather than hiding them |
| `/classify` | Ticket classification | A schema-constrained object; presets demonstrate spam, prompt injection and the blank-input 422 |
| `/ask` | Question answering over this repository's docs | The answer plus the passages behind it, with similarity scores, each openable in its source document |
| `/agent` | The agent loop | A run that picks its own tools, and pauses for your approval before it changes anything |

All four end at a **trace** control. `/agent` is the odd one out in shape: a run outlives the request that starts it, so that page follows a resource — it holds a blocking `GET /agent/{id}?wait=` open, which returns the moment the run wants a decision or finishes.

## Types are generated, never written

`app/lib/api.generated.ts` comes from `api/openapi.json`, which FastAPI derives from its pydantic models. `app/lib/api.ts` only aliases it and declares no shapes of its own, so there is no hand-written copy of any response to drift.

```bash
npm run gen:api     # regenerate after an API model changes
npm run check:api   # fails if the committed TypeScript is stale
```

Both files are committed and neither is edited by hand. A field renamed in `api/tracing.py` fails `check:api` instead of quietly leaving the trace panel a field short.

## Shared parts

Four pages that each invented their own vocabulary would say the same thing four ways, so the distinctions live in one place:

- **`components/Composer.tsx`** — the input, fixed to the bottom of the viewport on every page. Pages pad their own bottom so content scrolls behind it.
- **`components/Notice.tsx`** — `ok` / `info` / `warn` / `danger`, and the `Label` used for every section heading. `info` is deliberately not `danger`: a model declining to answer is a correct outcome and should not wear the colour of a broken request.
- **`components/TraceDrawer.tsx`** — one drawer for the whole app, opened from anywhere, overlaying from the right so the page behind it does not reflow. It renders trace steps generically, so a step kind the API adds needs no change here.
- **`components/Markdown.tsx`** — prose the model wrote is rendered; records of what the system did stay literal. A source document is rendered but never escaped, because escaping would shift the offsets used to highlight a passage inside it.
- **`components/RetrievalConfig.tsx`** — reads the API's similarity floor at runtime from `GET /retrieval/config` rather than keeping a number here. With no floor readable, scores are shown with no verdict rather than judged against a guess.

## This is Next.js 16

[`AGENTS.md`](AGENTS.md) carries a block written by `next dev` warning that this version differs from older ones in APIs and conventions — consult `node_modules/next/dist/docs/` before writing Next code. Do not strip that block; `next dev` re-adds it. `layout.tsx` using the generated `LayoutProps<"/">` type instead of a hand-written props interface is one example of the difference.

## No tests

There are none beyond the build. `tsc`, `eslint` and `next build` are what CI runs for this directory; correctness of what the pages *say* is checked on the API side, where the eval suites live.
