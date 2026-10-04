# Session Handoff

## Repository State

- Branch: `main` (up to date with `origin/main`).
- HEAD when this snapshot was generated: `0dd0186` — `feat: polish OlistIQ messaging and visual theme`.
- Working tree: one uncommitted milestone plus this handoff:
  - modified: `frontend/src/App.tsx`, `frontend/src/App.css` (skill metadata on the six
    example-question buttons: typed `{question, labels}` data, `.example-question`/
    `.example-skill` render, `ⓘ` glyph, `.example` flex column).
  - `SESSION_HANDOFF.md` was updated by this snapshot; commit it with the above.
- Recent relevant commits:
  - `0dd0186` feat: polish OlistIQ messaging and visual theme
  - `b454f16` feat: improve OlistIQ onboarding
  - `4e20bb6` feat: add Olist dataset context
  - `9eb36fc` feat: redesign frontend as OlistIQ
  - `1482780` feat: add answer reveal and animated query status
  - `707b8da` feat: add markdown answer rendering
  - `437994d` feat: add frontend query transport

## Project State

- Stage: end-to-end single-question analytical agent (committed) with a streaming HTTP
  boundary (committed) and a complete recruiter-facing React frontend, "OlistIQ"
  (committed except the example-skill-metadata tweak).
- Flow: framework-neutral DB core (`psycopg`) → LangChain tool adapters →
  provider-neutral LangGraph agent → DeepSeek `deepseek-flash` (Responses API) →
  FastAPI SSE → React transport → rendered answer. Each request is independent.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`; no views.
- Decisions: `adr/ADR-001`…`ADR-006` (all Accepted). No frontend-specific ADR.

## Implemented State

- Ingestion (`ingest.py`): `COPY ... FROM STDIN` for the nine `data/raw/` CSVs (BOM
  stripped, CSV + header, empty→NULL) in one transaction; refuses populated tables;
  `--replace` truncates and reloads. CLI only.
- Schema introspection (`schema.py`): `get_schema(conn=None) -> DatabaseSchema` from
  `pg_catalog` (tables, columns, types, nullability, PK/FK, CHECKs, comments);
  `fetch_catalog()` is DB-bound, `build_schema()` is pure.
- Read-only SQL (`query.py`): `run_sql(sql, *, max_rows=500, timeout_ms=5000, conn=None)
  -> SqlResult`; lexical `validate_sql()` plus a read-only transaction, local timeouts,
  and a named server-side cursor; failures are structured, not raised.
- Tools (`tools.py`): `get_schema_tool` (`get_schema`) and `run_sql_tool` (`run_sql`,
  `sql: str`), returning the core `.to_dict()`.
- Agent (`agent.py`): `build_agent(model) -> CompiledStateGraph`; `MessagesState` only;
  nodes `agent`/`tools` (`ToolNode`) routed by `tools_condition`; `SYSTEM_PROMPT`
  enforces database evidence, schema-first, semantic-preserving SQL repair, scope
  preservation, stopping when sufficient, and evidence discipline.
- Model (`model.py`): `build_model() -> ChatOpenAI` for DeepSeek Responses API
  (`deepseek-flash`, `https://api.deepseek.com`, `use_responses_api=True`,
  `output_version="responses/v1"`, `reasoning={"effort": "high"}`), key from
  `DEEPSEEK_API_KEY`.
- Streaming (`streaming.py`): `translate_agent_events(events)` — pure async generator
  (no FastAPI/SSE) mapping LangGraph v2 events to `status`/`answer_delta`/`done`/`error`.
- HTTP (`api.py`): `POST /query` SSE via first-party `fastapi.sse`; lifespan builds the
  graph once and stores it on `app.state.agent`.
- Frontend transport (`api.ts`): `streamQuery(question) -> AsyncGenerator<QueryEvent>`
  using `fetch` + `ReadableStream` (not `EventSource`); discriminated union
  `status|answer_delta|done|error`; strict incremental SSE parsing; terminal events end
  the stream; malformed frames and premature EOF throw.
- Frontend UI (`App.tsx`): local `useReducer` query state
  (`phase: idle|running|complete|error`, `status`, `answer`, `error.kind:
  application|transport`); composer; six example questions (typed `{question, labels}`);
  hidden always-mounted `aria-live="polite"` latest-status region; single animated status
  (`✦` + text pulse, reduced-motion aware); result panel; complete-only Markdown via
  `AnswerReveal`; partial answers as plain text; generic user transport error with a
  `import.meta.env.DEV` console diagnostic.
- Markdown (`MarkdownAnswer.tsx`): `react-markdown` + `remark-gfm`; GFM tables wrapped in
  `.table-scroll`; images disabled; no `rehype-raw`.
- Reveal (`AnswerReveal.tsx`): `useLayoutEffect` sets `--reveal-delay` on top-level
  blocks; CSS stagger fade (280 ms, 70 ms step, cap 12).
- Branding: "OlistIQ"; `frontend/public/olistiq.svg` used for favicon and header mark;
  `<title>OlistIQ — Ask questions about Olist</title>`; light/dark `theme-color` metas;
  blue/cyan accent tokens matching the mark.

## Current Implementation Details

- Backend deps (`backend/pyproject.toml`): `fastapi>=0.142.2`, `langchain-core>=1.6.6`,
  `langchain-openai>=1.6.7`, `langgraph>=1.2.12`, `psycopg[binary]>=3.3.6`,
  `python-dotenv>=1.2.3`, `uvicorn>=0.54.0`; dev `httpx2>=2.13.1`, `pytest>=9.1.1`.
  Python `>=3.12`; build backend `uv_build`.
- Frontend deps (`frontend/package.json`): `react`/`react-dom` `^19.2.8`,
  `react-markdown` `^10.1.0`, `remark-gfm` `^4.0.1`; dev `vite` `^8.3.0`,
  `typescript` `~6.0.2`, `oxlint` `^1.81.0`. Scripts: `dev`, `build` (`tsc -b && vite
  build`), `lint` (oxlint), `preview`.
- Frontend TS constraints (`tsconfig.app.json`): `noUnusedLocals`/`noUnusedParameters`,
  `verbatimModuleSyntax` (type-only imports), `erasableSyntaxOnly` (no enums/param props).
- Config: `db.py`/`model.py` load repo-root `.env` (process env wins). DB needs
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model needs
  `DEEPSEEK_API_KEY`. Local gitignored `.env` uses `POSTGRES_PORT=5433`.
- HTTP contract: `POST /query` `{"question": str}` → SSE `status {message}`,
  `answer_delta {text}`, `done`, `error {message}`; client reconstructs the answer from
  `answer_delta.text`. Blank/missing question → HTTP 422. Statuses stream in real time
  with consecutive duplicates collapsed; the final answer is buffered per agent turn and
  emitted when the final turn completes (not token-by-token). Only `AIMessageChunk.text`
  is surfaced (no reasoning).
- Frontend contract: relative `POST /query`; Vite dev proxy maps `/query` →
  `http://127.0.0.1:8000` (`frontend/vite.config.ts`) so no CORS is needed. The dataset
  overview renders only while `phase === 'idle'`; sample questions fill the textarea
  (never auto-submit) and are disabled while running.
- Serialization: `SqlResult.to_dict()` = `{ok, columns, rows, row_count, truncated,
  error}` (`error` is `null` or `{kind, message}`, kind
  `validation|timeout|execution|unexpected`); `DatabaseSchema.to_dict()` tables with
  `{name, comment, primary_key, columns, foreign_keys, checks}`.
- Backend tests (`backend/tests/`): `test_ingest`, `test_schema`, `test_query`,
  `test_tools`, `test_agent`, `test_model`, `test_streaming`, `test_api`. DB-free by
  default; live DB `RUN_DB_TESTS=1`; live model `RUN_MODEL_TESTS=1` + key; API tests
  inject a fake agent via `create_app(agent_factory=...)`. Frontend has no test runner
  (only `build` + `lint`).
- Runtime entry: `uv run uvicorn ai_sql_analyst.api:app` from `backend/`; the
  `ai-sql-analyst` console script still points at placeholder `__init__.py:main`.
- Limitations: `get_schema()` raises on an unavailable DB (not structured, ADR-003); DB
  role is the image superuser with no `default_transaction_read_only` (ADR-002); no
  connection pooling; the read-only transaction still permits temporary tables;
  `fastapi` pulls an inert `opentelemetry-api`. Frontend: no `AbortSignal`/cancellation;
  no frontend test framework; answer delivered after the final agent turn, not
  token-by-token (deliberate, ADR-006). `frontend/public/icons.svg` and
  `frontend/src/assets/*` remain unused Vite leftovers.

## Recent Progress

- Redesigned the frontend as "OlistIQ": branding, `olistiq.svg` mark/favicon, blue/cyan
  accent token system matched to the mark, responsive single-column layout, light/dark.
- Added GFM Markdown answer rendering, block-reveal animation, single animated status,
  idle-only dataset overview, and six recruiter-facing sample questions.
- Added example-question skill labels (`filtering`/`grouping`/`aggregation`/
  `customer identity`/`multi-table join`/`date analysis`) — this milestone is the current
  uncommitted change.
- Polished hero/placeholder/hint copy and hid raw transport exception text behind a
  generic user message.

## Open / Unfinished State

- Example-question skill-metadata change is implemented and passing but uncommitted.
- No frontend test framework or automated frontend tests.
- No cancellation: a running query cannot be aborted; there is no `AbortSignal`.
- No persistence, checkpointing, sessions, or conversation memory; each `/query` is
  independent; no authentication.
- `get_schema()` failures are not surfaced as structured tool errors.
- No connection pooling; least-privilege database hardening not implemented.
- No MCP server; root `README.md` and `backend/README.md` are empty.
- Behavioral evaluation of the agent is manual; no formal evaluation harness.

## Verification

- `cd backend && uv run pytest -q` → 105 passed, 22 skipped.
- `RUN_DB_TESTS=1 uv run pytest -q` → 126 passed, 1 skipped (skip is the opt-in live
  DeepSeek model test).
- `cd frontend && npm run build` → passes; `npm run lint` → passes (includes the current
  uncommitted example-metadata change).
- PostgreSQL reachable: `ai-sql-analyst-postgres-1` (`postgres:16`, healthy, host port
  5433); nine Olist tables loaded.
- Browser (Playwright) checks this session covered: progressive single status + reveal,
  GFM table/code integrity with local scroll, error + partial-answer fallback, idle-only
  dataset, six sample buttons (click-to-fill only), light/dark theming, reduced motion,
  focus-visible rings, and 375 px no-horizontal-overflow.
- `git diff --check` clean.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-006` — finalized decisions.
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation.
- `backend/tests/` — executable backend verification.
- `frontend/src/api.ts`, `App.tsx`, `MarkdownAnswer.tsx`, `AnswerReveal.tsx` — frontend
  implementation; `frontend/vite.config.ts` — dev proxy.
- `frontend/package.json`, `backend/pyproject.toml` — dependencies/metadata.
- `data/README.md` — dataset provenance; `docker-compose.yml` — local Postgres.
- `.env.example` — configuration variables; `.github/workflows/ci.yml` — CI.

## Session Boundary

- HEAD at snapshot time is `0dd0186`; the only uncommitted milestone is the
  example-question skill metadata in `frontend/src/App.tsx` and `frontend/src/App.css`.
  `SESSION_HANDOFF.md` (this file) is also modified and should be committed with them.
- Runtime: the Docker `postgres` service is healthy on host port 5433; the local
  gitignored `.env` sets `POSTGRES_PORT=5433` and holds `DEEPSEEK_API_KEY` (never
  commit it). No `uvicorn` backend process is currently running; start it with the
  command above for live `/query` testing.
- The frontend dev proxy expects the backend at `http://127.0.0.1:8000`.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`.
