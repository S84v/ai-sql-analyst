# Session Handoff

## Repository State

- Branch: `main`
- HEAD when this snapshot was generated: `36561bf` — `feat: refine agent prompt and add prompt contract tests`
- Working tree: the streaming-boundary milestone is uncommitted:
  - modified: `backend/pyproject.toml`, `backend/uv.lock`
  - untracked: `adr/ADR-006-http-streaming-boundary.md`,
    `backend/src/ai_sql_analyst/api.py`, `backend/src/ai_sql_analyst/streaming.py`,
    `backend/tests/test_api.py`, `backend/tests/test_streaming.py`
  - `SESSION_HANDOFF.md` was updated by this snapshot; commit it with the above.
- Recent relevant commits:
  - `36561bf` feat: refine agent prompt and add prompt contract tests
  - `2c764f1` feat: add DeepSeek Responses API model integration
  - `521b1c2` feat: add LangGraph agent boundary

## Project State

- Stage: an end-to-end single-question analytical agent with a streaming HTTP
  boundary. The DB core, tools, agent, DeepSeek integration, and prompt policy
  are committed; the streaming boundary is implemented and tested but
  uncommitted.
- Flow: framework-neutral DB core (`psycopg`) → LangChain tool adapters →
  provider-neutral LangGraph agent → DeepSeek `deepseek-flash` (Responses API) →
  FastAPI SSE. Each request is independent.
- Backend package `backend/src/ai_sql_analyst/`: `db.py`, `schema.py`, `query.py`,
  `tools.py`, `ingest.py`, `agent.py`, `model.py`, `streaming.py`, `api.py`,
  `__init__.py`.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in the
  `public` schema; no views.
- Frontend: React + TypeScript + Vite scaffold; no backend integration.
- Decisions: `adr/ADR-001`…`ADR-006` (all Accepted); `data/README.md` documents
  dataset provenance.

## Implemented State

- Ingestion (`ingest.py`): `COPY ... FROM STDIN` for the nine `data/raw/` CSVs
  (BOM stripped, CSV + header, empty→NULL) in one transaction; refuses populated
  tables; `--replace` truncates and reloads. CLI only.
- Schema introspection (`schema.py`): `get_schema(conn=None) -> DatabaseSchema`
  from `pg_catalog` (tables, columns, types, nullability, PK/FK, CHECKs,
  comments); `fetch_catalog()` is DB-bound, `build_schema()` is pure.
- Read-only SQL (`query.py`): `run_sql(sql, *, max_rows=500, timeout_ms=5000,
  conn=None) -> SqlResult`; lexical `validate_sql()` plus a read-only
  transaction, local timeouts, and a named server-side cursor; failures are
  structured, not raised.
- Tools (`tools.py`): `get_schema_tool` (`get_schema`) and `run_sql_tool`
  (`run_sql`, `sql: str`), returning the core `.to_dict()`.
- Agent (`agent.py`): `build_agent(model) -> CompiledStateGraph`; `MessagesState`
  only; nodes `agent`/`tools` (`ToolNode`) routed by `tools_condition`;
  `SYSTEM_PROMPT` enforces database evidence, schema-first, semantic-preserving
  SQL repair, scope preservation, stopping when sufficient, and evidence
  discipline.
- Model (`model.py`): `build_model() -> ChatOpenAI` for DeepSeek Responses API
  (`deepseek-flash`, `https://api.deepseek.com`, `use_responses_api=True`,
  `output_version="responses/v1"`, `reasoning={"effort": "high"}`), key from
  `DEEPSEEK_API_KEY`.
- Streaming (`streaming.py`): `translate_agent_events(events)` — a pure async
  generator (no FastAPI/SSE) mapping LangGraph v2 events to
  `status`/`answer_delta`/`done`/`error`.
- HTTP (`api.py`): `POST /query` SSE via first-party `fastapi.sse`; lifespan
  builds the graph once and stores it on `app.state.agent`.

## Current Implementation Details

- Dependencies (`backend/pyproject.toml`): runtime `fastapi>=0.142.2`,
  `langchain-core>=1.6.6`, `langchain-openai>=1.6.7`, `langgraph>=1.2.12`,
  `psycopg[binary]>=3.3.6`, `python-dotenv>=1.2.3`, `uvicorn>=0.54.0`; dev
  `httpx2>=2.13.1`, `pytest>=9.1.1`. Python `>=3.12`; build backend `uv_build`.
- Configuration: `db.py`/`model.py` load repo-root `.env` (process env wins). DB
  needs `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` (optional host/port);
  the model needs `DEEPSEEK_API_KEY`. `.env.example` documents them; the local
  gitignored `.env` uses `POSTGRES_PORT=5433`.
- Serialization: `SqlResult.to_dict()` = `{ok, columns, rows, row_count,
  truncated, error}` (`error` is `null` or `{kind, message}` with kind
  `validation|timeout|execution|unexpected`); `DatabaseSchema.to_dict()` tables
  with `{name, comment, primary_key, columns, foreign_keys, checks}`.
- Agent stream contract: `astream(input, stream_mode=["messages", "updates"],
  version="v2")`.
- HTTP contract: `POST /query` `{"question": str}` → SSE `status {message}`,
  `answer_delta {text}`, `done`, `error {message}`; the client reconstructs the
  answer from `answer_delta.text`. Blank/missing question → HTTP 422. Statuses
  stream in real time with consecutive duplicates collapsed; the final answer is
  buffered per agent turn and emitted when the final turn completes (not
  token-by-token). Only `AIMessageChunk.text` is surfaced (no reasoning).
- Runtime entry: `uv run uvicorn ai_sql_analyst.api:app` from `backend/`; the
  `ai-sql-analyst` console script still points at placeholder `__init__.py:main`.
- Tests (`backend/tests/`): `test_ingest`, `test_schema`, `test_query`,
  `test_tools`, `test_agent`, `test_model`, `test_streaming`, `test_api`. DB-free
  by default; live DB `RUN_DB_TESTS=1`; live model `RUN_MODEL_TESTS=1` + key. API
  tests inject a fake agent via `create_app(agent_factory=...)`.
- Limitations: `get_schema()` raises on an unavailable DB (not structured,
  ADR-003); the DB role is the image superuser with no
  `default_transaction_read_only` (ADR-002); no connection pooling; the read-only
  transaction still permits temporary tables; `fastapi` pulls an inert
  `opentelemetry-api` dependency.

## Recent Progress

- Added the DeepSeek Responses API model integration (ADR-005): `model.py`,
  `test_model.py`, `langchain-openai`.
- Refined the agent `SYSTEM_PROMPT` and added prompt-contract tests
  (commit `36561bf`).
- Implemented the streaming HTTP boundary (ADR-006): `api.py`, `streaming.py`,
  `test_api.py`, `test_streaming.py`, plus `fastapi`/`uvicorn`/`httpx2` deps.
  Added per-turn answer buffering after a live run showed the model narrating
  before a tool call.

## Open / Unfinished State

- The streaming boundary (ADR-006 + `api.py`/`streaming.py` + tests + deps) is
  implemented and passing but uncommitted.
- No frontend integration (scaffold only); no authentication.
- No persistence, checkpointing, sessions, or conversation memory; each `/query`
  is independent.
- No connection pooling; least-privilege database hardening not implemented.
- `get_schema()` failures are not surfaced as structured tool errors.
- The final answer is delivered after the final agent turn completes, not
  token-by-token (deliberate; ADR-006).
- No MCP server; root `README.md` and `backend/README.md` are empty.
- Behavioral evaluation of the agent is manual; no formal evaluation harness.

## Verification

- `cd backend && uv run pytest -q` → 105 passed, 22 skipped.
- `RUN_DB_TESTS=1 uv run pytest -q` → 126 passed, 1 skipped (the skip is the
  opt-in live DeepSeek model test).
- `uv sync --locked --dev` resolves cleanly (CI lock consistency).
- PostgreSQL reachable: `ai-sql-analyst-postgres-1` (`postgres:16`, healthy, host
  port 5433); nine Olist tables loaded (orders 99,441; geolocation 1,000,163).
- Manual live check (this session): `uvicorn ai_sql_analyst.api:app` + `POST
  /query` against the real `deepseek-flash` and local database streamed ordered
  statuses and a correct final answer with no reasoning leakage.
- `git diff --check` clean.

## Authoritative References

- `AGENTS.md` — workflow, engineering rules, source-of-truth hierarchy.
- `adr/README.md`, `adr/ADR-001`…`adr/ADR-006` — finalized decisions.
- `backend/sql/schema.sql` — physical schema and schema comments.
- `backend/src/ai_sql_analyst/` — implementation.
- `backend/tests/` — executable verification.
- `backend/pyproject.toml` — dependencies and project metadata.
- `data/README.md` — dataset provenance; `docker-compose.yml` — local Postgres.
- `.env.example` — configuration variables; `.github/workflows/ci.yml` — CI.

## Session Boundary

- HEAD at snapshot time is `36561bf`; the streaming milestone is uncommitted.
  Modified tracked files: `backend/pyproject.toml`, `backend/uv.lock`,
  `SESSION_HANDOFF.md`. Untracked: `adr/ADR-006-http-streaming-boundary.md`,
  `backend/src/ai_sql_analyst/{api,streaming}.py`,
  `backend/tests/{test_api,test_streaming}.py`.
- Runtime: the Docker `postgres` service is healthy on host port 5433; the local
  gitignored `.env` sets `POSTGRES_PORT=5433` and holds `DEEPSEEK_API_KEY` (never
  commit it).
- The streaming boundary uses FastAPI first-party SSE (`fastapi.sse`, present in
  fastapi 0.142.2) and Starlette 1.7's `httpx2`-based test client.
- `data/raw/` holds the nine Olist CSVs (gitignored) plus `.gitkeep`.
