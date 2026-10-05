# Frontend — OlistIQ SPA

The OlistIQ single-page app is where a user asks a natural-language question and
watches the agent work. It is built with React + TypeScript + Vite and talks to
the backend over a single SSE endpoint. For the project overview, see the
[root README](../README.md).

## Stack

- React + React DOM
- TypeScript
- Vite
- `react-markdown` + `remark-gfm` (answer rendering)
- oxlint (linting)

## Getting started

```bash
cd frontend
npm ci
npm run dev
```

The dev server prints a local URL (default `http://localhost:5173`). Start the
backend first.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the Vite dev server. |
| `npm run build` | Type-check (`tsc -b`) and build for production. |
| `npm run lint` | Run oxlint. |
| `npm run preview` | Serve the production build locally. |

## Dev proxy

The SPA always uses the relative URL `/query`. In development, Vite proxies it to
the local FastAPI backend, so no CORS configuration is needed:

```text
/query → http://127.0.0.1:8000
```

This is a **development-only** arrangement; there is no production hosting or
CORS setup in this repository.

## Transport boundary (`src/api.ts`)

`streamQuery(question)` is an async generator that POSTs to `/query` and parses
the Server-Sent Events stream. It knows only the application-level protocol, not
LangGraph or DeepSeek internals.

Application events:

| Event | Payload | Meaning |
| --- | --- | --- |
| `status` | `{ message }` | Concise progress derived from tool activity. |
| `answer_delta` | `{ text }` | A piece of the final answer to concatenate. |
| `done` | — | Terminal: the answer is complete. |
| `error` | `{ message }` | Terminal: the analysis failed. |

The parser is strict:

- SSE frames are split on blank lines; `:` comment lines (keepalives) are
  ignored, and joined `data:` lines are parsed as JSON.
- Malformed JSON, invalid event shapes, an unterminated final frame, or EOF
  without a terminal event are treated as protocol errors and throw.
- `done` and `error` are terminal — the generator stops reading, so anything sent
  afterwards is ignored.
- HTTP failures are rejected before the stream body is read.

The app converts a transport/protocol failure into a generic user-facing message
and logs the raw detail to the console in development only.

## Answer rendering

`MarkdownAnswer` renders the final answer as GitHub-Flavored Markdown using
`react-markdown` + `remark-gfm`:

- Markdown is compiled to React elements — no `dangerouslySetInnerHTML`, and no
  `rehype-raw`, so raw HTML in model output is ignored.
- Images are disabled, so untrusted output cannot trigger external image loads.
- Wide tables are wrapped in a scroll container instead of widening the page.

`AnswerReveal` applies a staggered CSS reveal to the top-level blocks of the
already-rendered answer. It never parses incomplete Markdown.

## User-facing behavior

- The dataset overview is shown only while idle.
- Example-question buttons fill the textarea (they never auto-submit) and are
  disabled while a query runs.
- A single animated status line shows progress; `Ctrl`/`⌘` + `Enter` submits.
- The latest status is announced through a visually hidden `aria-live` region.
- Reduced-motion preferences are respected; focus-visible styles are present.
- Light/dark theming follows the system color scheme.

## TypeScript constraints

`tsconfig.app.json` enforces a few rules that affect how code must be written:

- `noUnusedLocals` / `noUnusedParameters` — unused symbols fail the build.
- `verbatimModuleSyntax` — type-only imports must use `import type`.
- `erasableSyntaxOnly` — no TypeScript-only runtime syntax (enums, parameter
  properties).

## Verification

There is **no automated frontend test runner**. The current checks are:

```bash
npm run build
npm run lint
```

`build` runs the TypeScript compiler in addition to bundling, so it is the
primary correctness gate.
