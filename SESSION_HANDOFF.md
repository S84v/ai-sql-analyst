# Session Handoff

## Repository State

- Branch: `main` (up to date with `origin/main`).
- HEAD when this snapshot was generated: `e04c9d0` — `docs: refine README visual presentation`.
- Working tree: clean before this snapshot; `SESSION_HANDOFF.md` (this file) is the only
  file modified by this snapshot.
- Recent relevant commits:
  - `e04c9d0` docs: refine README visual presentation
  - `d67709a` docs: polish presentation and add project visuals
  - `ac7856c` docs: add project visual assets
  - `7167190` docs: document project setup, architecture, and dataset
  - `1c3b0a3` chore: clean repository template artifacts
  - `1ebd698` feat: add MCP tool adapter

## Project State

- Stage: complete end-to-end single-question analytical agent with an additive MCP
  adapter and a fully documented repository.
- Application flow: framework-neutral DB core (`psycopg`) → LangChain tool adapters →
  provider-neutral LangGraph agent → DeepSeek `deepseek-flash` (Responses API) →
  FastAPI SSE → React transport → rendered answer. Each request is independent.
- MCP path: `mcp_server.py` (stdio) → `schema.get_schema()` / `query.run_sql()` →
  PostgreSQL. It is a separate edge adapter, independent of FastAPI and LangGraph.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`; no views.
- Decisions: `adr/ADR-001`…`adr/ADR-007` (all Accepted), plus `adr/README.md`.
- Documentation: root, backend, frontend, and data READMEs are written; SVG/GIF/Mermaid
  visuals live in `docs/images/`.

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
- Model (`model.py`): `build_model() -> ChatOpenAI` for the DeepSeek Responses API
  (`deepseek-flash`, `https://api.deepseek.com`, `use_responses_api=True`,
  `output_version="responses/v1"`, `reasoning={"effort": "high"}`), key from
  `DEEPSEEK_API_KEY`.
- Streaming (`streaming.py`): `translate_agent_events(events)` — pure async generator
  (no FastAPI/SSE) mapping LangGraph v2 events to `status`/`answer_delta`/`done`/`error`.
- HTTP (`api.py`): `POST /query` SSE via first-party `fastapi.sse`; lifespan builds the
  graph once and stores it on `app.state.agent`.
- MCP (`mcp_server.py`): `MCPServer("ai-sql-analyst")` (MCP Python SDK v2) exposing
  `get_schema` and `run_readonly_sql`; delegates to `schema.get_schema()` /
  `query.run_sql(sql)`; stdio transport; console script `ai-sql-analyst-mcp`. Imports
  only `mcp`, `schema`, `query`.
- Frontend (`api.ts`, `App.tsx`, `MarkdownAnswer.tsx`, `AnswerReveal.tsx`): `fetch` +
  `ReadableStream` SSE transport (discriminated `status|answer_delta|done|error`, strict
  incremental parsing, terminal events end the stream); `useReducer` query state; composer
  with six example questions; `aria-live` status; complete-only GFM Markdown
  (`react-markdown` + `remark-gfm`, scrollable tables, images disabled, no `rehype-raw`);
  staggered reveal. Branding "OlistIQ", `frontend/public/olistiq.svg` favicon/README logo.
- Documentation/visuals: READMEs for root/backend/frontend/data; `docs/images/` holds
  `architecture.mmd`/`.svg`, `erdiagram.mmd`/`.svg`, and `olist-front-page.gif`. The root
  README has a centered header (logo, badges, demo GIF) and embeds `architecture.svg`;
  `data/README.md` embeds `erdiagram.svg`.

## Current Implementation Details

- Backend deps (`backend/pyproject.toml`): `fastapi>=0.142.2`, `langchain-core>=1.6.6`,
  `langchain-openai>=1.6.7`, `langgraph>=1.2.12`, `mcp>=2.3.0`, `psycopg[binary]>=3.3.6`,
  `python-dotenv>=1.2.3`, `uvicorn>=0.54.0`; dev `httpx2>=2.13.1`, `pytest>=9.1.1`.
  Python `>=3.12`; build backend `uv_build`. Only console script is
  `ai-sql-analyst-mcp`; `__init__.py` is an empty package marker.
- Frontend deps (`frontend/package.json`): `react`/`react-dom` `^19.2.8`,
  `react-markdown` `^10.1.0`, `remark-gfm` `^4.0.1`; dev `vite` `^8.3.0`,
  `typescript` `~6.0.2`, `oxlint` `^1.81.0`. Scripts: `dev`, `build`
  (`tsc -b && vite build`), `lint`, `preview`. TS (`tsconfig.app.json`):
  `noUnusedLocals`/`noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`.
- Config: `db.py`/`model.py` load the repo-root `.env` (process env wins). DB needs
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model needs
  `DEEPSEEK_API_KEY`. Local gitignored `.env` sets `POSTGRES_PORT=5433`.
- HTTP contract: `POST /query` `{"question": str}` → SSE `status {message}`,
  `answer_delta {text}`, `done`, `error {message}`; blank/missing question → HTTP 422.
  Statuses stream in real time with consecutive duplicates collapsed; the final answer is
  buffered per agent turn and emitted when the final turn completes (not token-by-token);
  only `AIMessageChunk.text` is surfaced (no reasoning).
- MCP contract: only `sql` is client-controlled; `max_rows`/`timeout_ms`/`conn` are not
  exposed. `run_sql` structured errors pass through as normal results; an unexpected
  `get_schema()` failure becomes a sanitized generic MCP tool error.
- Frontend contract: relative `POST /query`; Vite dev proxy maps `/query` →
  `http://127.0.0.1:8000` (`frontend/vite.config.ts`), so no CORS is needed.
- Serialization: `SqlResult.to_dict()` = `{ok, columns, rows, row_count, truncated,
  error}` (`error` is `null` or `{kind, message}`, kind
  `validation|timeout|execution|unexpected`); `DatabaseSchema.to_dict()` tables with
  `{name, comment, primary_key, columns, foreign_keys, checks}`.
- Backend tests (`backend/tests/`): `test_ingest`, `test_schema`, `test_query`,
  `test_tools`, `test_agent`, `test_model`, `test_streaming`, `test_api`, `test_mcp`.
  DB-free by default; live DB `RUN_DB_TESTS=1`; live model `RUN_MODEL_TESTS=1` + key; API
  tests inject a fake agent via `create_app(agent_factory=...)`; MCP tests use the SDK
  in-memory client. Frontend has no test runner (only `build` + `lint`).
- Limitations: `get_schema()` raises on an unavailable DB (not structured, ADR-003); DB
  role is the image superuser with no `default_transaction_read_only` (ADR-002); no
  connection pooling; the read-only transaction still permits temporary tables; frontend
  has no `AbortSignal`/cancellation; answer delivered after the final agent turn (ADR-006);
  `frontend/src/assets/hero.png` and `frontend/public/icons.svg` remain unused leftovers;
  `fastapi` pulls an inert `opentelemetry-api`.

## Recent Progress

- Added the MCP adapter milestone (`mcp_server.py`, `test_mcp.py`, ADR-007), exposing
  `get_schema` and `run_readonly_sql` over stdio on top of the existing safe functions.
- Repository hygiene: removed the tracked `.pyc`, extended `.gitignore` with
  `.venv/`/`.pytest_cache/`, removed the placeholder `ai-sql-analyst` console script and
  template `__init__` code, corrected the `pyproject.toml` description, and deleted the
  unused Vite template assets (`react.svg`, `vite.svg`).
- Wrote the root/backend/frontend/data READMEs and added project visuals
  (`docs/images/architecture.*`, `erdiagram.*`, `olist-front-page.gif`), then polished the
  root README header (centered logo + badges + demo GIF) and embedded the architecture and
  ER diagrams.

## Open / Unfinished State

- No frontend test framework or automated frontend tests.
- No cancellation: a running query cannot be aborted; there is no `AbortSignal`.
- No authentication, authorization, or rate limiting; no CORS configuration (dev relies on
  the Vite proxy); MCP is local/stdio only with no public hosting.
- No persistence, checkpointing, sessions, or conversation memory; each `/query` is
  independent.
- `get_schema()` failures are not surfaced as structured tool errors.
- No connection pooling; least-privilege database hardening not implemented.
- Behavioral evaluation of the agent is manual; no formal evaluation harness.

## Verification

- `cd backend && uv run pytest -q` → 113 passed, 24 skipped.
- `RUN_DB_TESTS=1 uv run pytest -q` → 136 passed, 1 skipped (skip is the opt-in live
  DeepSeek model test).
- `RUN_MODEL_TESTS=1 RUN_DB_TESTS=1 uv run pytest -q` → 137 passed (opt-in; needs
  `DEEPSEEK_API_KEY` and network).
- `cd frontend && npm run build` → passes; `npm run lint` → clean.
- PostgreSQL reachable: `ai-sql-analyst-postgres-1` (`postgres:16`, healthy, host port
  5433); nine Olist tables loaded.
- Repo hygiene confirmed: no tracked `.pyc`, `.venv/`/`.pytest_cache/` ignored, only the
  `ai-sql-analyst-mcp` console script remains, README image/source links resolve.
- `git diff --check` clean.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-007` — finalized decisions.
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation.
- `backend/tests/` — executable backend verification.
- `frontend/src/` (`api.ts`, `App.tsx`, `MarkdownAnswer.tsx`, `AnswerReveal.tsx`) and
  `frontend/vite.config.ts` — frontend implementation and dev proxy.
- `frontend/package.json`, `backend/pyproject.toml` — dependencies/metadata.
- `README.md`, `backend/README.md`, `frontend/README.md`, `data/README.md` — documentation.
- `docs/images/` — architecture and data-model visuals plus the demo GIF.
- `docker-compose.yml` — local Postgres; `.env.example` — configuration; `.github/workflows/` — CI.

## Session Boundary

- HEAD at snapshot time is `e04c9d0`; the working tree was clean before this snapshot, and
  the only change made by this snapshot is `SESSION_HANDOFF.md` itself.
- Runtime: the Docker `postgres` service is healthy on host port 5433; the local gitignored
  `.env` sets `POSTGRES_PORT=5433` and holds `DEEPSEEK_API_KEY` (never commit it). No
  `uvicorn` backend process is currently running; start it with the command above for live
  `/query` testing.
- The frontend dev proxy expects the backend at `http://127.0.0.1:8000`.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`.
