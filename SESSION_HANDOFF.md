# Session Handoff

## Repository State

- Branch: `main`, up to date with `origin/main`
  (`4a713ce8a89a0bb715b39dc6f77cec2bf50ea8a3`).
- HEAD when this snapshot was generated: `4a713ce` — `test: add information tab coverage`.
- Working tree: clean before this snapshot; `SESSION_HANDOFF.md` (this file) is the only
  change made by this snapshot.
- Recent relevant commits: `4a713ce` test: add information tab coverage; `5a62cae` feat: add
  project information tabs; `bd123ff` test: add frontend unit and component tests; `ffff1fe`
  feat: add OpenTelemetry observability; `c9ebf83` fix: set explicit production agent
  recursion limit; `ae8e432` fix: bound agent recovery after SQL timeouts.

## Project State

- Stage: complete end-to-end single-question analytical agent with hardened timeout and
  recursion boundaries, opt-in OpenTelemetry observability, an additive MCP adapter, an
  opt-in DeepEval evaluation suite, and a tested React frontend.
- Flow: React + TypeScript + Vite SPA → FastAPI `POST /query` (SSE) → LangGraph agent →
  LangChain tool adapters → framework-neutral PostgreSQL core → grounded answer; each
  request is independent. Provider is DeepSeek `deepseek-flash` (Responses API, only in
  `model.py`). MCP is a separate stdio edge (`mcp_server.py` → `schema`/`query` → PostgreSQL).
- Observability: OpenTelemetry (official FastAPI + GenAI-LangChain instrumentation plus a
  manual `run_sql` span/metrics); export opt-in and off by default.
- Evaluation: golden dataset → DeepEval LangGraph `CallbackHandler` → real agent →
  PostgreSQL → deterministic Olist checks + one limited GEval judge.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`; no views.
- Decisions: `adr/ADR-001`…`adr/ADR-010` (all Accepted) plus `adr/README.md`.
- CI: two independent jobs — `backend-tests` (pytest) and `frontend-tests` (npm ci, lint,
  build, test).

## Implemented State

- DB core (`ingest.py`, `schema.py`, `query.py`): `COPY ... FROM STDIN` loader for the nine
  `data/raw/` CSVs (single transaction; refuses populated tables; `--replace`);
  `get_schema(conn=None) -> DatabaseSchema` from `pg_catalog`; `run_sql(sql, *, max_rows=500,
  timeout_ms=5000, conn=None) -> SqlResult` with lexical validation, a read-only transaction,
  local timeouts, a named server-side cursor, and structured (non-raising) failures. `run_sql`
  also emits one OTel span and the two SQL metrics through the OTel API only.
- Agent (`tools.py`, `agent.py`, `model.py`): `get_schema`/`run_sql` tools return `to_dict()`;
  `build_agent(model)` uses `MessagesState` with `agent`/`tools` (`ToolNode`) and
  `tools_condition` routing, plus a `timeout_stop` terminal node reached at
  `TIMEOUT_BUDGET = 3` total timeouts; `SYSTEM_PROMPT` enforces evidence, schema-first,
  semantic-preserving repair, scope preservation, and stop-when-sufficient; `build_model()`
  constructs the DeepSeek Responses-API `ChatOpenAI`.
- Adapters: `streaming.py` maps LangGraph v2 events to `status`/`answer_delta`/`done`/`error`
  (incl. the timeout-stop message); `api.py` serves `POST /query` SSE, builds the graph once,
  sets `_RECURSION_LIMIT = 25`, and records `olistiq.run.outcome` on the HTTP span;
  `mcp_server.py` exposes `get_schema`/`run_readonly_sql` over stdio (`ai-sql-analyst-mcp`).
- `observability.py`: idempotent setup; official FastAPI + GenAI-LangChain instrumentation
  (`skip_dep_check=True`; the `langchain` meta-package is intentionally absent); opt-in
  OTLP/HTTP or stderr-console export (off by default → no providers or auto-instrumentation);
  unconditional `NO_CONTENT`; `RunOutcome`/`watch_run`/`record_outcome`.
- Frontend (`src/`): SSE transport (`api.ts`), `useReducer` query state (`App.tsx`),
  complete-only GFM Markdown with staggered reveal, and `InfoTabs` (accessible
  Data/Project/Contact tabs; none selected initially; roving tabindex; animation replays per
  switch; contact action pills) replacing the old dataset section after the examples.
- Evaluation (`backend/evals/`): 20-case golden set, deterministic Olist metrics, one GEval
  judge, and a runner over DeepEval's LangGraph `CallbackHandler`; regression tests in
  `backend/tests/test_eval_metrics.py`.
- Tests: backend `backend/tests/` (incl. `test_observability.py`); frontend Vitest + React
  Testing Library (`src/api.test.ts`, `src/App.test.tsx`, `src/components/InfoTabs.test.tsx`).

## Current Implementation Details

- Backend deps (`backend/pyproject.toml`): `fastapi>=0.142.2`, `langchain-core>=1.6.6`,
  `langchain-openai>=1.6.7`, `langgraph>=1.2.12`, `mcp>=2.3.0`,
  `opentelemetry-api>=1.45.1`, `opentelemetry-sdk>=1.45.1`,
  `opentelemetry-instrumentation-fastapi>=0.66b1`,
  `opentelemetry-instrumentation-genai-langchain>=1.2b0`,
  `opentelemetry-exporter-otlp-proto-http>=1.45.1`, `psycopg[binary]>=3.3.6`,
  `python-dotenv>=1.2.3`, `uvicorn>=0.54.0`; dev `httpx2>=2.13.1`, `pytest>=9.1.1`; optional
  `evals` group `deepeval>=4.2.8` (kept out of `dev`). Python `>=3.12` (`uv_build`); console
  script `ai-sql-analyst-mcp`.
- Frontend deps (`frontend/package.json`): `react`/`react-dom` `^19.2.8`,
  `react-markdown ^10.1.0`, `remark-gfm ^4.0.1`; dev `vite ^8.3.0`, `@vitejs/plugin-react
  ^6.1.1`, `typescript ~6.0.2`, `oxlint ^1.81.0`, `vitest ^5.0.3`, `jsdom ^30.1.2`,
  `@testing-library/react ^16.3.3`, `@testing-library/jest-dom ^7.0.1`,
  `@testing-library/dom ^10.4.2`, `@types/node`, `@types/react`, `@types/react-dom`.
- Contracts: `POST /query` `{"question": str}` → SSE `status`/`answer_delta`/`done`/`error`
  (blank → HTTP 422; final answer buffered per agent turn; only `AIMessageChunk.text`, no
  reasoning). `run_sql` returns structured `{ok, columns, rows, row_count, truncated,
  error}` with `error` `null` or `{kind, message}` (`validation|timeout|execution|
  unexpected`). `DbSchema.to_dict()` tables with `{name, comment, primary_key, columns,
  foreign_keys, checks}`. `olistiq.run.outcome` is one of `success`, `no_answer`,
  `timeout_stop`, `recursion_limit`, `error`. SQL spans carry `db.system`, `db.operation`
  (`SELECT|WITH|VALUES|TABLE` only), `olistiq.sql.outcome`, `olistiq.sql.truncated`,
  `olistiq.sql.row_count`; metrics `olistiq.sql.executions` and `olistiq.sql.duration` use
  the `outcome` (and `truncated`) dimension. No question/SQL/rows/prompt/completion content
  is captured.
- Config: `db.py`/`model.py` load the repo-root `.env` (process env wins). DB needs
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model needs
  `DEEPSEEK_API_KEY`. Local gitignored `.env` sets `POSTGRES_PORT=5433`. Observability env:
  `OTEL_EXPORTER_OTLP_ENDPOINT`, `OLISTIQ_OTEL_CONSOLE`, `OTEL_SERVICE_NAME`.
- Tests: backend DB-free by default (live DB `RUN_DB_TESTS=1`; live model `RUN_MODEL_TESTS=1`
  + key); API tests inject a fake agent via `create_app(agent_factory=...)`; observability
  tests use in-memory OTel providers; MCP tests use the SDK in-memory client. Frontend
  commands (from `frontend/`): `npm run dev`, `npm run build` (`tsc -b && vite build`),
  `npm run lint` (oxlint), `npm test` (vitest run). TS constraints: `noUnusedLocals`/
  `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`.
- Evaluation is opt-in/live: `uv sync --group evals` then from `backend/`
  `uv run python evals/run_evals.py` (`--no-judge`, `--case <id>`, `--validate`,
  `--recursion-limit`); needs a live database and `DEEPSEEK_API_KEY`. Exit `0` only when all
  deterministic checks pass; the GEval judge is informational. Reports land in
  `backend/evals/reports/` (gitignored).
- Limitations: `get_schema()` raises (not structured) on an unavailable DB (ADR-003); the DB
  role is the image superuser with no `default_transaction_read_only` (ADR-002); no
  connection pooling; the read-only transaction still permits temporary tables; no frontend
  cancellation; the answer is delivered after the final agent turn (ADR-006); observability
  content capture is disabled by design but the GenAI instrumentor is a beta release
  (`1.x-b`); export has no built-in backend (external OTLP endpoint required);
  `frontend/src/assets/hero.png` and `frontend/public/icons.svg` are unused leftovers.

## Recent Progress

- Hardened SQL timeouts (ADR-009: total `TIMEOUT_BUDGET = 3` + deterministic `timeout_stop`
  node) and set an explicit production `recursion_limit = 25` at the FastAPI boundary.
- Added OpenTelemetry observability (ADR-010): `observability.py`, official FastAPI +
  GenAI-LangChain instrumentation, manual `run_sql` span + metrics, HTTP run-outcome
  classification, opt-in export, and unconditional `NO_CONTENT` capture. Live inspection
  confirmed the HTTP → workflow → model/tool → SQL tree with the SQL span nested under
  `execute_tool run_sql`.
- Added the frontend test suite (Vitest + RTL + jsdom) with a separate CI `frontend-tests`
  job, and replaced the homepage dataset section with the accessible `InfoTabs` area.

## Open / Unfinished State

- `backend/evals/README.md` still records the pre-ADR-009 baseline (19/20); the current
  deterministic baseline is 20/20 (see Verification). The README was not updated.
- Evaluation remains live and opt-in and is not part of CI.
- The previously observed intermittent recursion-limit exhaustion on `delivery_worsened_why`
  was diagnosed as model-behaviour variability (variable-length but convergent); no code
  change was made, and the timeout guard does not cover it.
- No authentication, authorization, CORS configuration, or rate limiting; MCP is local/stdio
  only.
- No persistence, checkpointing, sessions, or conversation memory; each `/query` is
  independent.
- No connection pooling and no least-privilege database hardening.
- `npm audit` reports one high-severity advisory in the transitive dev-only
  `source-map-js@1.2.1` (via `vite`/`postcss` and `jsdom`/`css-tree`); not addressed.

## Verification

- `cd backend && uv run pytest -q` → 145 passed, 24 skipped (skips are the opt-in live
  database/model tests).
- `cd frontend && npm test` → 38 passed (3 files); `npm run lint` → clean;
  `npm run build` → passes (`tsc -b && vite build`).
- `cd backend && uv run python evals/run_evals.py --validate` → dataset OK, 20 cases
  (sha256 `d33ddedb54f395d7ea99e3e5c1ba4d547e4f3898ecfd0f80d1322a933d773b41`).
- `cd backend && uv run python evals/run_evals.py --no-judge` → 20/20 deterministic cases
  passed.
- `git diff --check` → clean.
- PostgreSQL `ai-sql-analyst-postgres-1` (`postgres:16`) healthy on host port 5433; nine Olist
  tables loaded.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-010` — finalized decisions.
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation (incl. `observability.py`).
- `backend/tests/` — executable backend verification (incl. `test_observability.py`,
  `test_eval_metrics.py`).
- `backend/evals/`, `backend/evals/README.md` — golden dataset, metrics, judge, runner.
- `frontend/src/` (`api.ts`, `App.tsx`, `components/InfoTabs.tsx`, `MarkdownAnswer.tsx`,
  `AnswerReveal.tsx`) and `frontend/vite.config.ts` — frontend implementation and dev proxy.
- `frontend/package.json`, `backend/pyproject.toml` — dependencies/metadata.
- `README.md`, `backend/README.md`, `frontend/README.md`, `data/README.md` — documentation.
- `docs/images/` — architecture/data-model visuals and the demo GIF.
- `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml` — infrastructure and CI.

## Session Boundary

- HEAD at snapshot time is `4a713ce`; the working tree was clean before this snapshot, and the
  only change made by this snapshot is `SESSION_HANDOFF.md` itself.
- All implementation work from this session is already committed through `4a713ce`
  (`ae8e432` timeout hardening, `c9ebf83` production recursion limit, `ffff1fe`
  observability, `bd123ff` frontend tests + CI job, `5a62cae`/`4a713ce` information tabs).
  No partially completed or uncommitted implementation work remains.
- Runtime: the Docker `postgres` service is healthy on host port 5433; the local gitignored
  `.env` sets `POSTGRES_PORT=5433` and holds `DEEPSEEK_API_KEY` (never commit it). No
  `uvicorn` process is running; the frontend dev proxy expects the backend at
  `http://127.0.0.1:8000`.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`; `backend/evals/reports/`
  and `frontend/dist/` hold gitignored generated output.
