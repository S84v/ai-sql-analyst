# Session Handoff

## Repository State

- Branch `main`, up to date with `origin/main` (`d57818305434d5fb0f99ec9c249a068459f16248`).
- HEAD when this snapshot was generated: `d578183` — `ci: skip deployments for Markdown-only changes`.
- Working tree clean at generation time; `SESSION_HANDOFF.md` (this file) is the only change
  made by this snapshot.
- Recent relevant commits: `d578183` skip deployments for Markdown-only changes; `f70719c`
  correct repository guidance and standardize OlistIQ branding; `fe0135a` record verified
  production deployment state; `fa78981` verify Cloud Run image digest against amd64
  manifest; `47b3b80` restore CI checks on push to main; `ee7d40d` add GitHub Actions CD for
  frontend and backend.

## Project State

- Stage: OlistIQ is a production-deployed, end-to-end single-question analytical agent over
  the Olist dataset, with automated CI/CD and a documented production runbook.
- Flow: React + TypeScript + Vite SPA → FastAPI `POST /query` (SSE) → LangGraph agent →
  LangChain tool adapters → framework-neutral PostgreSQL core → grounded answer; each
  request is independent. Provider is DeepSeek `deepseek-flash` (Responses API, only in
  `model.py`). MCP is a separate stdio edge (`mcp_server.py`).
- Production (ADR-011): the static SPA is served by Firebase Hosting; the browser calls Cloud
  Run directly over HTTPS/SSE (`/query` is not proxied through Hosting); Cloud Run reaches
  Neon PostgreSQL 16 and the DeepSeek API.
- CI/CD: `.github/workflows/ci.yml` runs on pull requests and pushes to `main`; component-
  scoped CD (`deploy-backend.yml`, `deploy-frontend.yml`) authenticates with Workload
  Identity Federation and deploys by immutable image digest.
- Decisions: `adr/ADR-001`…`adr/ADR-011` (all Accepted) plus `adr/README.md`.

## Implemented State

- DB core (`ingest.py`, `schema.py`, `query.py`): `COPY ... FROM STDIN` loader for the nine
  `data/raw/` CSVs (single transaction; refuses populated tables; `--replace`);
  `get_schema(conn=None) -> DatabaseSchema` from `pg_catalog`; `run_sql(sql, *,
  max_rows=500, timeout_ms=5000, conn=None) -> SqlResult` with lexical validation, a
  read-only transaction, local timeouts, a named server-side cursor, and structured
  (non-raising) failures.
- Agent (`tools.py`, `agent.py`, `model.py`): `get_schema`/`run_sql` tools return
  `to_dict()`; `build_agent(model)` uses `MessagesState` with `agent`/`tools` (`ToolNode`)
  and `tools_condition` routing, plus a `timeout_stop` terminal node reached at
  `TIMEOUT_BUDGET = 3` total timeouts; `build_model()` constructs the DeepSeek Responses-API
  `ChatOpenAI` (the only provider-specific module).
- Adapters: `streaming.py` maps LangGraph v2 events to `status`/`answer_delta`/`done`/`error`
  (incl. the timeout-stop message); `api.py` serves `POST /query` SSE, builds the graph once
  at lifespan, sets `_RECURSION_LIMIT = 25`, parses `CORS_ALLOWED_ORIGINS` (exact origins;
  wildcard raises), and records `olistiq.run.outcome`; `mcp_server.py` exposes
  `get_schema`/`run_readonly_sql` over stdio (console script `ai-sql-analyst-mcp`).
- `observability.py`: idempotent OpenTelemetry setup (official FastAPI + GenAI-LangChain
  instrumentation, `skip_dep_check=True`); opt-in OTLP/HTTP or stderr-console export (off by
  default → inert); unconditional `NO_CONTENT`; `RunOutcome`/`watch_run`/`record_outcome`.
- Frontend (`src/`): SSE transport (`api.ts` resolves `VITE_API_ORIGIN` at call time, using
  relative `/query` by default), `useReducer` query state (`App.tsx`), complete-only GFM
  Markdown with staggered reveal, and `InfoTabs` (rendered unconditionally; the query-result
  section is conditional).
- Evaluation (`backend/evals/`): 20-case golden set, deterministic Olist metrics, one GEval
  judge, and a runner over DeepEval's LangGraph `CallbackHandler`; regression tests in
  `backend/tests/test_eval_metrics.py`.
- Docs: root/`backend`/`frontend`/`data` READMEs, `adr/` (ADR-001…011), and
  `docs/deployment.md` (production runbook with live revision/digests and CD run records).
- Tests: backend `backend/tests/` (11 files, incl. `test_observability.py`); frontend Vitest
  + React Testing Library (`src/api.test.ts`, `src/App.test.tsx`,
  `src/components/InfoTabs.test.tsx`).

## Current Implementation Details

- Contracts: `POST /query` `{"question": str}` → SSE `status`/`answer_delta`/`done`/`error`
  (blank → HTTP 422; final answer buffered per agent turn; only `AIMessageChunk.text`, no
  reasoning). `run_sql` returns `{ok, columns, rows, row_count, truncated, error}` with
  `error` null or `{kind, message}` (`validation|timeout|execution|unexpected`).
  `DbSchema.to_dict()` tables `{name, comment, primary_key, columns, foreign_keys, checks}`.
- Config: repo-root `.env` (process env wins; gitignored). DB requires
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model requires
  `DEEPSEEK_API_KEY`; optional runtime `CORS_ALLOWED_ORIGINS`; OTel env
  `OTEL_EXPORTER_OTLP_ENDPOINT`/`OLISTIQ_OTEL_CONSOLE`/`OTEL_SERVICE_NAME`.
- Database: local PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`
  (`backend/sql/schema.sql`); no views. Production database is Neon PostgreSQL 16.
- CI: `backend-tests` (`uv sync --locked --dev` + `uv run pytest`) and `frontend-tests`
  (`npm ci`, `npm run lint`, `npm run build`, `npm test`).
- CD: `deploy-backend.yml` (push on `backend/**` excluding `*.md`, or `workflow_dispatch`;
  tests → build/push image tagged with the commit SHA → `gcloud run services update --image`
  by immutable digest → verify ready revision, digests, preserved config, CORS preflight) and
  `deploy-frontend.yml` (push on `frontend/**` excluding `*.md`, `firebase.json`,
  `.firebaserc`, or `workflow_dispatch`; pinned `firebase-tools@15.33.0` + ADC preflight →
  Hosting deploy → URL check). Both deploy jobs depend on `test`, run only on
  `refs/heads/main`, and use `id-token: write`.
- Limitations: `get_schema()` raises (not structured) on an unavailable DB; the local Docker
  DB role is the image superuser and production role privileges are not independently
  verified; no connection pooling; the read-only transaction still permits temporary tables;
  no persistence/checkpointing/memory; the answer is delivered after the final agent turn;
  observability content capture is disabled by design but the GenAI instrumentor is a beta
  release; `frontend/src/assets/hero.png` and `frontend/public/icons.svg` are unused
  leftovers.

## Recent Progress

- Added component-scoped GitHub Actions CD for the backend and frontend (WIF auth, immutable
  digest deploy, post-deploy verification) and excluded Markdown-only component changes from
  deployment triggers.
- Recorded the verified production state in `docs/deployment.md`: revision
  `olistiq-api-00005-p6b`, the service-template image-index digest vs. the resolved
  `linux/amd64` platform-manifest digest, and the successful CD/CI runs.
- Fixed the Cloud Run digest verification to compare the served revision digest against the
  `linux/amd64` child manifest of the exact pushed image index.
- Corrected documentation: standardized the product name to OlistIQ, fixed the AGENTS.md
  backend entry point, added `observability.py` to the backend module map, corrected a stale
  frontend behavior claim, and clarified the README database-role and repository-layout text.

## Open / Unfinished State

- The OlistIQ branding update covered documentation only; "AI SQL Analyst" still appears in
  source docstrings/comments (`backend/sql/schema.sql`, `backend/src/ai_sql_analyst/db.py`,
  `backend/src/ai_sql_analyst/schema.py`, `frontend/src/api.ts`).
- Evaluation is live/opt-in and outside CI.
- Client-disconnect cancellation has not been independently verified with an ASGI disconnect
  test.
- Budget-alert settings and cold-start/instance-count metrics were not independently
  verified.
- MCP is local/stdio only; no persistence, checkpointing, conversation memory, or sessions
  (each request is independent); no connection pooling.

## Verification

- `cd backend && uv run pytest -q` → 150 passed, 24 skipped (skips are the opt-in live
  database/model tests).
- `cd frontend && npm test` → 43 passed (3 files); `npm run lint` → clean; `npm run build`
  → passes (`tsc -b && vite build`).
- `cd backend && uv run python evals/run_evals.py --validate` → dataset OK, 20 cases (sha256
  `d33ddedb54f395d7ea99e3e5c1ba4d547e4f3898ecfd0f80d1322a933d773b41`).
- `git diff --check` → clean.
- Docker `ai-sql-analyst-postgres-1` (`postgres:16`) healthy; nine Olist tables in `public`
  and no views.
- Production revision/digests and the successful backend/frontend/CD runs are recorded in
  `docs/deployment.md`.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-011` — finalized decisions.
- `docs/deployment.md` — production runbook (revision, digests, CD runs, rollback).
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation (incl. `observability.py`).
- `backend/tests/` — executable backend verification.
- `backend/evals/`, `backend/evals/README.md` — golden dataset, metrics, judge, runner.
- `frontend/src/` and `frontend/README.md` — SPA and transport.
- `data/README.md` — dataset provenance and SQL caveats.
- `docker-compose.yml`, `.env.example` — local infrastructure and configuration template.
- `.github/workflows/` — CI and component-scoped CD.

## Session Boundary

- HEAD at snapshot time is `d578183`; the working tree was clean when this snapshot was
  generated, and the only change it introduces is `SESSION_HANDOFF.md` itself.
- All implementation/documentation work from this session is already committed through
  `d578183` (production deployment docs, OlistIQ branding + guidance corrections,
  index→platform digest verification fix, component-scoped CD, and Markdown-only path
  filters). No partially completed or uncommitted implementation work remains.
- Runtime: the Docker `ai-sql-analyst-postgres-1` (`postgres:16`) service is healthy; the
  local gitignored `.env` holds the local DB settings and `DEEPSEEK_API_KEY` (never commit
  it). Nothing is listening on `127.0.0.1:8000` (no backend dev server running); the Vite dev
  proxy expects the backend there.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`; `backend/evals/reports/`
  and `frontend/dist/` hold gitignored generated output.
