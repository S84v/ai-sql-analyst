# Session Handoff

## Repository State

- Branch `main`, up to date with `origin/main` (`595b52207b08a84de12b402fcb755c922018874c`).
- HEAD when this snapshot was generated: `595b522` — `docs: align repository docs with current
  implementation`.
- Working tree clean at generation time; `SESSION_HANDOFF.md` (this file) is the only change made
  by this snapshot.
- Recent relevant commits: `595b522` doc alignment; `92b67ad` per-client query rate limiting;
  `0671fa7` contact email prefill; `96ee173` query-result smooth scroll. (The temporary client-IP
  diagnostic commits `98a94ab`/`4630dbc` were fully removed in `92b67ad`; no diagnostic remains.)

## Project State

- Stage: a production-deployed, end-to-end single-question analytical agent over the Olist
  dataset, with automated CI/CD, a documented production runbook, and a best-effort
  process-local per-client rate limiter.
- Flow: React + TypeScript + Vite SPA → FastAPI `POST /query` (SSE) → LangGraph agent →
  LangChain tool adapters → framework-neutral PostgreSQL core → grounded answer; each request is
  independent. Provider is DeepSeek `deepseek-flash` (Responses API, only in `model.py`); MCP is
  a separate stdio edge (`mcp_server.py`).
- Production (ADR-011): the static SPA is served by Firebase Hosting; the browser calls Cloud Run
  directly over HTTPS/SSE (`/query` is not proxied through Hosting); Cloud Run reaches Neon
  PostgreSQL 16 and the DeepSeek API.
- CI/CD: `.github/workflows/ci.yml` runs on pull requests and pushes to `main`; component-scoped
  CD (`deploy-backend.yml`, `deploy-frontend.yml`) authenticates with Workload Identity
  Federation and deploys by immutable image digest.
- Decisions: `adr/ADR-001`…`adr/ADR-012` (all Accepted) plus `adr/README.md`.

## Implemented State

- DB core (`ingest.py`, `schema.py`, `query.py`): `COPY ... FROM STDIN` loader for the nine
  `data/raw/` CSVs (single transaction; refuses populated tables; `--replace`);
  `get_schema(conn=None) -> DatabaseSchema` from `pg_catalog`; `run_sql(sql, *, max_rows=500,
  timeout_ms=5000, conn=None) -> SqlResult` with lexical validation, a read-only transaction,
  local timeouts, a named server-side cursor, and structured (non-raising) failures.
- Agent (`tools.py`, `agent.py`, `model.py`): `get_schema`/`run_sql` tools return `to_dict()`;
  `build_agent(model)` uses `MessagesState` with `agent`/`tools` (`ToolNode`) and
  `tools_condition` routing, plus a `timeout_stop` terminal node reached at `TIMEOUT_BUDGET = 3`
  total timeouts; `build_model()` constructs the DeepSeek Responses-API `ChatOpenAI` (the only
  provider-specific module).
- Adapters: `streaming.py` maps LangGraph v2 events to `status`/`answer_delta`/`done`/`error`
  (incl. the timeout-stop message); `api.py` serves `POST /query` SSE, builds the graph once at
  lifespan, sets `_RECURSION_LIMIT = 25`, parses `CORS_ALLOWED_ORIGINS` (exact origins; wildcard
  raises), enforces the rate limit as a route dependency, and records `olistiq.run.outcome`;
  `mcp_server.py` exposes `get_schema`/`run_readonly_sql` over stdio (console script
  `ai-sql-analyst-mcp`).
- Rate limiting (`ratelimit.py`): process-local fixed-window counter (10 requests / 60 s per
  client key) with an injectable `time.monotonic` clock, a `threading.Lock`, an expiry sweep (no
  background task), and `RateLimitDecision` (allowed + whole-second `retry_after`).
  `client_key_from_forwarded_for` keys on the rightmost validated `X-Forwarded-For` entry
  (stdlib `ipaddress`), falling back to a shared `unknown` key.
- `observability.py`: idempotent OpenTelemetry setup (official FastAPI + GenAI-LangChain
  instrumentation, `skip_dep_check=True`); opt-in OTLP/HTTP or stderr-console export (off by
  default → inert); unconditional `NO_CONTENT`; `RunOutcome`/`watch_run`/`record_outcome`.
- Frontend (`src/`): SSE transport (`api.ts` resolves `VITE_API_ORIGIN` at call time, relative
  `/query` by default); `useReducer` query state (`App.tsx`); complete-only GFM Markdown with
  staggered reveal; `InfoTabs` (rendered unconditionally; the query-result section is
  conditional). Submitting a question scrolls the result section into view once per submission
  (instant under `prefers-reduced-motion`, smooth otherwise); the Contact tab's Email link is a
  `mailto:` prefilled with subject `About OlistIQ` and a short body.
- Evaluation (`backend/evals/`): 20-case golden set, deterministic Olist metrics, one GEval
  judge, and a runner over DeepEval's LangGraph `CallbackHandler`; regression tests in
  `backend/tests/test_eval_metrics.py`.
- Docs: root/`backend`/`frontend`/`data` READMEs, `adr/` (ADR-001…ADR-012), and
  `docs/deployment.md` (production runbook with the live revision/digests and CD run records).
- Tests: backend `backend/tests/` (12 files, incl. `test_observability.py`, `test_ratelimit.py`);
  frontend Vitest + React Testing Library (3 files under `src/`).

## Current Implementation Details

- Contracts: `POST /query` `{"question": str}` → SSE `status`/`answer_delta`/`done`/`error`
  (blank → HTTP 422; final answer buffered per agent turn; only `AIMessageChunk.text`, no
  reasoning). `run_sql` returns `{ok, columns, rows, row_count, truncated, error}` with `error`
  null or `{kind, message}` (`validation|timeout|execution|unexpected`). `DbSchema.to_dict()`
  tables `{name, comment, primary_key, columns, foreign_keys, checks}`.
- Rate limiting: a route dependency on `POST /query` only, enforced before the agent runs or the
  SSE stream starts. Excess requests → HTTP 429 + `Retry-After`; `OPTIONS /query` preflight is
  not limited. Process-local, resets on restart, and can multiply across Cloud Run instances; the
  rightmost-`X-Forwarded-For` key is documented and not spoof-proof (ADR-012).
  `create_app(rate_limiter=...)` injects a limiter with a fake clock for deterministic tests.
- Config: repo-root `.env` (process env wins; gitignored). DB requires
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model requires
  `DEEPSEEK_API_KEY`; optional runtime `CORS_ALLOWED_ORIGINS`; opt-in OTel env
  `OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_SERVICE_NAME`/`OLISTIQ_OTEL_CONSOLE`. The limiter has no
  configuration flags.
- Database: local PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`
  (`backend/sql/schema.sql`); no views. Production database is Neon PostgreSQL 16.
- CI: `backend-tests` (`uv sync --locked --dev` + `uv run pytest`) and `frontend-tests`
  (`npm ci`, `npm run lint`, `npm run build`, `npm test`).
- CD: component-scoped, main-only, immutable-digest deploys with post-deploy verification;
  `deploy-backend.yml` triggers on `backend/**` (excluding `*.md`) or `workflow_dispatch`,
  `deploy-frontend.yml` on `frontend/**` (excluding `*.md`), `firebase.json`, `.firebaserc`, or
  `workflow_dispatch`.
- Limitations: `get_schema()` raises (not structured) on an unavailable DB; the local Docker DB
  role is the image superuser and production role privileges are not independently verified; no
  connection pooling; the read-only transaction still permits temporary tables; no
  persistence/checkpointing/memory; the answer is delivered after the final agent turn;
  observability content capture is disabled by design but the GenAI instrumentor is a beta
  release; `frontend/src/assets/hero.png` and `frontend/public/icons.svg` are unused leftovers.

## Recent Progress

- Added query-result smooth scrolling: submitting a question scrolls the result section into view
  once per submission, instant under `prefers-reduced-motion` and smooth otherwise.
- Prefilled the Contact tab's Email link with subject `About OlistIQ` and a short body.
- Added a temporary client-IP diagnostic to observe deployed request headers, then fully removed
  it once the rightmost-`X-Forwarded-For` behavior was established.
- Implemented ADR-012: best-effort, process-local per-client rate limiting for `POST /query`
  (`ratelimit.py` + route dependency), with unit and API tests.
- Aligned documentation with the implementation (root/`backend`/`frontend`/`data` READMEs:
  ADR-012, rate limiting, observability env vars, frontend scroll/email behavior) and refreshed
  `docs/deployment.md` with the live Cloud Run revision/digests.

## Open / Unfinished State

- "AI SQL Analyst" still appears in source docstrings/comments (`backend/sql/schema.sql`,
  `backend/src/ai_sql_analyst/db.py`, `backend/src/ai_sql_analyst/schema.py`,
  `frontend/src/api.ts`).
- The rate limiter is best-effort only: process-local, resets on restart, multiplies across
  instances, shared `unknown` bucket, non-spoof-proof rightmost-`X-Forwarded-For` key; no
  persistence or metrics.
- Evaluation is live/opt-in and outside CI.
- Client-disconnect cancellation has not been independently verified with an ASGI disconnect test.
- Budget-alert settings and cold-start/instance-count metrics were not independently verified.
- MCP is local/stdio only; no persistence, checkpointing, conversation memory, or sessions (each
  request is independent); no connection pooling.

## Verification

- `cd backend && uv run pytest -q` → 165 passed, 24 skipped (skips are the opt-in live
  database/model tests).
- `cd frontend && npm test` → 47 passed (3 files); `npm run lint` → clean; `npm run build` →
  passes (`tsc -b && vite build`).
- `cd backend && uv run python evals/run_evals.py --validate` → dataset OK, 20 cases (sha256
  `d33ddedb54f395d7ea99e3e5c1ba4d547e4f3898ecfd0f80d1322a933d773b41`).
- `git diff --check` → clean.
- Docker `ai-sql-analyst-postgres-1` (`postgres:16`) healthy; nine Olist tables in `public` and
  no views.
- Live Cloud Run re-verified read-only: ready revision `olistiq-api-00009-n4v` (the `92b67ad`
  build), 100% traffic; index/platform digests and the successful `92b67ad` deploy are recorded
  in `docs/deployment.md`.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-012` — finalized decisions.
- `docs/deployment.md` — production runbook (revision, digests, CD runs, rollback).
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation (incl. `observability.py`,
  `ratelimit.py`).
- `backend/tests/` — executable backend verification.
- `backend/evals/`, `backend/evals/README.md` — golden dataset, metrics, judge, runner.
- `frontend/src/` and `frontend/README.md` — SPA and transport.
- `data/README.md` — dataset provenance and SQL caveats.
- `docker-compose.yml`, `.env.example` — local infrastructure and configuration template.
- `.github/workflows/` — CI and component-scoped CD.

## Session Boundary

- HEAD at snapshot time is `595b522`; the working tree was clean when this snapshot was generated,
  and the only change it introduces is `SESSION_HANDOFF.md` itself.
- All implementation and documentation work is committed through `595b522` (rate limiting +
  ADR-012, email prefill, query-result smooth scroll, documentation alignment, refreshed
  deployment runbook). No partially completed or uncommitted implementation work remains.
- The temporary client-IP diagnostic is fully removed (no middleware, helpers, or tests remain).
- Runtime: the Docker `ai-sql-analyst-postgres-1` (`postgres:16`) service is healthy; the local
  gitignored `.env` holds the local DB settings and `DEEPSEEK_API_KEY` (never commit it). Nothing
  is listening on `127.0.0.1:8000` (no backend dev server running); the Vite dev proxy expects the
  backend there.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`; `backend/evals/reports/` and
  `frontend/dist/` hold gitignored generated output.
