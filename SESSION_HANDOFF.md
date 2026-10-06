# Session Handoff

## Repository State

- Branch: `main` (up to date with `origin/main`).
- HEAD when this snapshot was generated: `80df43c` — `test: add agent evaluation suite`.
- Working tree: clean before this snapshot; `SESSION_HANDOFF.md` (this file) is the only
  file modified by this snapshot.
- Recent relevant commits: `80df43c` test: add agent evaluation suite; `1a2e162` docs:
  update session handoff; `e04c9d0` docs: refine README visual presentation; `d67709a`
  docs: polish presentation and add project visuals; `7167190` docs: document project
  setup, architecture, and dataset.

## Project State

- Stage: complete end-to-end single-question analytical agent, an additive MCP adapter,
  and an opt-in DeepEval-based agent evaluation suite, over a documented repository.
- Application flow: framework-neutral DB core (`psycopg`) → LangChain tool adapters →
  provider-neutral LangGraph agent → DeepSeek `deepseek-flash` (Responses API) →
  FastAPI SSE → React transport → rendered answer. Each request is independent.
- Evaluation flow: golden dataset → DeepEval LangGraph `CallbackHandler` → real agent →
  PostgreSQL (existing `run_sql`) → deterministic Olist checks + one limited GEval judge.
- MCP path: `mcp_server.py` (stdio) → `schema.get_schema()` / `query.run_sql()` →
  PostgreSQL; a separate edge adapter, independent of FastAPI and LangGraph.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in `public`; no views.
- Decisions: `adr/ADR-001`…`adr/ADR-008` (all Accepted), plus `adr/README.md`.
- Documentation: root/backend/frontend/data/eval READMEs; SVG/GIF/Mermaid visuals in
  `docs/images/`.

## Implemented State

- Database core (`ingest.py`, `schema.py`, `query.py`): `COPY ... FROM STDIN` loader for the
  nine `data/raw/` CSVs (single transaction; refuses populated tables; `--replace`);
  `get_schema(conn=None) -> DatabaseSchema` from `pg_catalog` (tables, columns, types,
  nullability, PK/FK, CHECKs, comments; `fetch_catalog` DB-bound, `build_schema` pure);
  `run_sql(sql, *, max_rows=500, timeout_ms=5000, conn=None) -> SqlResult` with lexical
  `validate_sql()`, a read-only transaction, local timeouts, a named server-side cursor,
  and structured (non-raising) failures.
- Agent path (`tools.py`, `agent.py`, `model.py`): `get_schema_tool`/`run_sql_tool`
  (`sql: str`) return the core `.to_dict()`; `build_agent(model) -> CompiledStateGraph`
  uses `MessagesState` only with `agent`/`tools` (`ToolNode`) routed by `tools_condition`
  and a `SYSTEM_PROMPT` enforcing evidence, schema-first, semantic-preserving SQL repair,
  scope preservation, stopping when sufficient, and evidence discipline; `build_model()`
  builds the DeepSeek `deepseek-flash` Responses-API `ChatOpenAI`
  (`use_responses_api=True`, `output_version="responses/v1"`, `reasoning={"effort": "high"}`),
  key from `DEEPSEEK_API_KEY`.
- Adapters: `streaming.py` `translate_agent_events` (pure async generator mapping LangGraph
  v2 events to `status`/`answer_delta`/`done`/`error`); `api.py` `POST /query` SSE via
  first-party `fastapi.sse` (graph built once at lifespan); `mcp_server.py`
  `MCPServer("ai-sql-analyst")` exposing `get_schema` and `run_readonly_sql` over stdio
  (console script `ai-sql-analyst-mcp`).
- Frontend (`api.ts`, `App.tsx`, `MarkdownAnswer.tsx`, `AnswerReveal.tsx`): `fetch` +
  `ReadableStream` SSE transport (discriminated `status|answer_delta|done|error`);
  `useReducer` query state; six example questions; `aria-live` status; complete-only GFM
  Markdown (`react-markdown` + `remark-gfm`); staggered reveal.
- Evaluation (`backend/evals/`): `dataset.jsonl` (20 golden cases); `metrics.py`
  deterministic Olist checks (oracle through the existing `run_sql`, positional row
  comparison, customer-identity/fanout/translation/grain, safety, truncation/scope,
  timeout/fabrication); `judge.py` one GEval over a `DeepEvalBaseLLM` wrapping
  `build_model()` (same Responses API); `run_evals.py` attaches DeepEval's native LangGraph
  `CallbackHandler`, runs the real agent once per case, and writes JSON reports. Evaluator
  regression tests: `backend/tests/test_eval_metrics.py`.

## Current Implementation Details

- Backend deps (`backend/pyproject.toml`): `fastapi>=0.142.2`, `langchain-core>=1.6.6`,
  `langchain-openai>=1.6.7`, `langgraph>=1.2.12`, `mcp>=2.3.0`, `psycopg[binary]>=3.3.6`,
  `python-dotenv>=1.2.3`, `uvicorn>=0.54.0`; dev `httpx2>=2.13.1`, `pytest>=9.1.1`; optional
  `evals` group `deepeval>=4.2.8` (kept out of `dev`). Python `>=3.12`; `uv_build`; only
  console script `ai-sql-analyst-mcp`.
- Evaluation is opt-in/live: `uv sync --group evals`, then `uv run python evals/run_evals.py`
  (`--no-judge`, `--case <id>`, `--validate`); needs a live database and `DEEPSEEK_API_KEY`.
  Exit `0` only when all required deterministic checks pass; the GEval judge is
  informational and does not affect the exit code. DeepEval telemetry is disabled; no
  Confident AI; reports land in `backend/evals/reports/` (gitignored).
- Config: `db.py`/`model.py` load the repo-root `.env` (process env wins). DB needs
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port); model needs
  `DEEPSEEK_API_KEY`. Local gitignored `.env` sets `POSTGRES_PORT=5433`.
- Contracts: `POST /query` `{"question": str}` → SSE `status`/`answer_delta`/`done`/`error`
  (blank → HTTP 422; final answer buffered per agent turn; only `AIMessageChunk.text`, no
  reasoning); MCP exposes only `sql` (no `max_rows`/`timeout_ms`/`conn`) and sanitizes an
  unexpected `get_schema()` failure; the frontend uses a relative `POST /query` via the Vite
  dev proxy (no CORS).
- Serialization: `SqlResult.to_dict()` = `{ok, columns, rows, row_count, truncated, error}`
  (`error` is `null` or `{kind, message}`, kind `validation|timeout|execution|unexpected`);
  `DatabaseSchema.to_dict()` tables with `{name, comment, primary_key, columns,
  foreign_keys, checks}`.
- Backend tests (`backend/tests/`): `test_ingest`, `test_schema`, `test_query`, `test_tools`,
  `test_agent`, `test_model`, `test_streaming`, `test_api`, `test_mcp`, `test_eval_metrics`.
  DB-free by default; live DB `RUN_DB_TESTS=1`; live model `RUN_MODEL_TESTS=1` + key; API
  tests inject a fake agent via `create_app(agent_factory=...)`; MCP tests use the SDK
  in-memory client. Frontend has no test runner (`build` + `lint` only); TS
  `noUnusedLocals`/`noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`.
- Limitations: `get_schema()` raises on an unavailable DB (not structured, ADR-003); DB role
  is the image superuser with no `default_transaction_read_only` (ADR-002); no connection
  pooling; the read-only transaction still permits temporary tables; frontend has no
  cancellation; answer delivered after the final agent turn (ADR-006);
  `frontend/src/assets/hero.png` and `frontend/public/icons.svg` are unused leftovers;
  `fastapi` pulls an inert `opentelemetry-api`.

## Recent Progress

- Added the DeepEval-based agent-evaluation milestone: `adr/ADR-008`, the `backend/evals/`
  package (20-case golden dataset, deterministic Olist metrics, one GEval judge over the
  DeepSeek Responses API, and a runner using DeepEval's native LangGraph `CallbackHandler`),
  deterministic evaluator regression tests (`test_eval_metrics.py`), an optional `evals`
  dependency group, and root/eval documentation recording the 19/20 live baseline.

## Open / Unfinished State

- The intentionally expensive geolocation case fails the deterministic baseline: it reaches
  the query timeout and can exhaust the tool loop without an answer (`timeout_recovery`).
  This is a known robustness boundary; the agent has not been hardened for it.
  Recursion-limit exhaustion was also observed intermittently on a hard/underspecified question.
- Evaluation is live and opt-in (needs a database and `DEEPSEEK_API_KEY`) and is not part of CI.
- No frontend test framework or automated frontend tests.
- No cancellation: a running query cannot be aborted; there is no `AbortSignal`.
- No authentication, authorization, or rate limiting; no CORS configuration (dev relies on the
  Vite proxy); MCP is local/stdio only with no public hosting.
- No persistence, checkpointing, sessions, or conversation memory; each `/query` is independent.
- `get_schema()` failures are not surfaced as structured tool errors.
- No connection pooling; least-privilege database hardening not implemented.

## Verification

- `cd backend && uv run pytest -q` → 120 passed, 24 skipped (skips are the opt-in live
  database/model tests).
- `cd backend && uv run python evals/run_evals.py --validate` → dataset OK, 20 cases
  (sha256 `d33ddedb…`).
- Live full evaluation (`uv run python evals/run_evals.py`) → 19/20 deterministic cases passed
  (exit 1); the single failure is `expensive_geolocation_join` (`timeout_recovery`). The
  `--no-judge` run also reported 19/20. The GEval judge reported only for the two subjective cases.
- `uv sync --locked --dev` excludes DeepEval (optional `evals` group); the evaluator regression
  tests are deterministic and call no DeepSeek/PostgreSQL.
- `cd frontend && npm run build` → passes; `npm run lint` → clean (frontend unchanged this session).
- PostgreSQL `ai-sql-analyst-postgres-1` (`postgres:16`) healthy on host port 5433; nine Olist
  tables loaded.
- `git diff --check` clean.

## Authoritative References

- `AGENTS.md` — workflow and engineering rules.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-008` — finalized decisions.
- `backend/sql/schema.sql` — physical schema and schema comments/semantics.
- `backend/src/ai_sql_analyst/` — backend implementation.
- `backend/tests/` — executable backend verification (incl. `test_eval_metrics.py`).
- `backend/evals/`, `backend/evals/README.md` — golden dataset, deterministic metrics, judge,
  runner, and evaluation methodology/baseline.
- `frontend/src/` (`api.ts`, `App.tsx`, `MarkdownAnswer.tsx`, `AnswerReveal.tsx`) and
  `frontend/vite.config.ts` — frontend implementation and dev proxy.
- `frontend/package.json`, `backend/pyproject.toml` — dependencies/metadata.
- `README.md`, `backend/README.md`, `frontend/README.md`, `data/README.md` — documentation.
- `docs/images/` — architecture and data-model visuals plus the demo GIF.
- `docker-compose.yml` — local Postgres; `.env.example` — configuration; `.github/workflows/` — CI.

## Session Boundary

- HEAD at snapshot time is `80df43c`; the working tree was clean before this snapshot, and the
  only change made by this snapshot is `SESSION_HANDOFF.md` itself.
- This session's evaluation work is already committed as `80df43c`
  (`test: add agent evaluation suite`), and `main` is up to date with `origin/main`.
- Runtime: the Docker `postgres` service is healthy on host port 5433; the local gitignored
  `.env` sets `POSTGRES_PORT=5433` and holds `DEEPSEEK_API_KEY` (never commit it). No `uvicorn`
  backend process is running; start it per `README.md` for live `/query` testing.
- The frontend dev proxy expects the backend at `http://127.0.0.1:8000`.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`; `backend/evals/reports/`
  holds gitignored generated reports.
