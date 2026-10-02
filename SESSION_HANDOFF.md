# Session Handoff

## Repository State

- Branch: `main`
- HEAD: `522ddf9` — `docs: remove obsolete external reference docs`
- Working tree: contains one untracked file, `SESSION_HANDOFF.md` (this file);
  no staged, unstaged, or other untracked changes.
- Recent relevant commits:
  - `da1c291` feat: add LangChain database tool adapters
  - `29c4d16` feat: add read-only SQL execution
  - `9e9157f` docs: document read-only SQL execution boundry
  - `e675bf5` docs: establish architecture decision records
  - `c9dd550` feat: add PostgreSQL schema introspection
  - `522ddf9` docs: remove obsolete external reference docs

## Project State

- Stage: database foundation complete. Implemented and committed: Olist ingestion,
  physical schema, dynamic schema introspection, a read-only SQL execution
  boundary, and LangChain tool adapters over the core. No agent/model
  integration, HTTP API, MCP server, or frontend integration exists yet.
- Architecture: a framework-neutral Python core (`psycopg`, `python-dotenv`)
  talks to PostgreSQL 16 (Docker Compose); a thin `langchain-core` tool edge
  wraps the core for future LLM use.
- Backend package: `backend/src/ai_sql_analyst/` with `db.py`, `schema.py`,
  `query.py`, `tools.py`, `ingest.py`, `__init__.py`.
- Database: PostgreSQL 16 via `docker-compose.yml`; nine Olist tables in the
  `public` schema; no views.
- Frontend: React + TypeScript + Vite scaffold in `frontend/` (npm); unchanged
  by recent milestones.
- Docs/decisions: `adr/ADR-001`, `ADR-002`, `ADR-003` (all Accepted);
  `data/README.md` documents dataset provenance.

## Implemented State

- Ingestion (`backend/src/ai_sql_analyst/ingest.py`): loads the nine
  `data/raw/` CSVs via `COPY ... FROM STDIN` (binary chunk streaming, UTF-8 BOM
  stripped, `FORMAT csv`, `HEADER true`, empty fields → NULL); runs in one
  transaction; refuses to run when target tables already contain data; `--replace`
  truncates and reloads (resetting the geolocation identity). CLI only
  (`--replace`, `--raw-dir`).
- Schema introspection (`backend/src/ai_sql_analyst/schema.py`):
  `get_schema(conn=None) -> DatabaseSchema` reads `pg_catalog` for tables,
  columns, exact types, nullability, primary keys, foreign keys, CHECK
  definitions, and table/column comments. `fetch_catalog()` is the only
  DB-bound step; `build_schema()` is pure. Output is sorted deterministically.
- Read-only execution (`backend/src/ai_sql_analyst/query.py`):
  `run_sql(sql, *, max_rows=500, timeout_ms=5000, conn=None) -> SqlResult`.
  `validate_sql()` is a pure lexical pre-check (allows leading
  `SELECT`/`WITH`/`VALUES`/`TABLE`/`(`). Execution uses its own transaction with
  `SET TRANSACTION READ ONLY`, `SET LOCAL statement_timeout`, `SET LOCAL
  lock_timeout` (half the statement timeout, omitted when `timeout_ms == 1`), and
  a named server-side cursor. Results are fetched with `fetchmany(max_rows + 1)`
  and flagged via `truncated`.
- LangChain tools (`backend/src/ai_sql_analyst/tools.py`): two `@tool`
  adapters — `get_schema_tool` (model-facing name `get_schema`, no arguments) and
  `run_sql_tool` (model-facing name `run_sql`, single `sql: str`) — each
  returning the corresponding core `.to_dict()`.

## Current Implementation Details

- Dependencies (`backend/pyproject.toml`): runtime `langchain-core>=1.6.6`,
  `psycopg[binary]>=3.3.6`, `python-dotenv>=1.2.3`; dev `pytest>=9.1.1`.
  Python `>=3.12`; build backend `uv_build`; console script `ai-sql-analyst`.
- Configuration: `db.py` loads repo-root `.env` (`load_dotenv`), with process
  env taking precedence; required `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`; `POSTGRES_HOST` (default `localhost`), `POSTGRES_PORT`
  (default `5432`). The local gitignored `.env` uses `POSTGRES_PORT=5433`
  (host conflict on 5432); `.env.example` documents `5432`.
- `SqlResult.to_dict()` shape:
  `{ok, columns, rows, row_count, truncated, error}` where `error` is
  `null` or `{kind, message}` and `kind` ∈ `validation|timeout|execution|unexpected`.
- `DatabaseSchema.to_dict()` shape:
  `{tables: [{name, comment, primary_key, columns: [{name, data_type, nullable,
  comment}], foreign_keys: [{columns, references_table, references_columns}],
  checks}]}`.
- Schema highlights: IDs/ZIP prefixes/state are `text`; money `numeric(12,2)`;
  product measurements `integer`; timestamps are `timestamp` (naive);
  `geolocation` uses a surrogate `bigint GENERATED ALWAYS AS IDENTITY` PK;
  `order_reviews` PK is `(review_id, order_id)`; `products.product_category_name`
  has no FK to the translation table; comments carry the semantic caveats.
- Testing: `backend/tests/` — `test_ingest.py`, `test_schema.py`,
  `test_query.py`, `test_tools.py`. Tests are database-free by default; live
  integration tests are opt-in with `RUN_DB_TESTS=1` and otherwise skip.
- Local commands: run from `backend/` with `uv` (`uv run pytest`,
  `uv run python -m ai_sql_analyst.ingest`, `uv add ...`).
- Current limitations affecting development: `get_schema()` raises on an
  unavailable database (the tool layer does not convert this to a structured
  error; recorded in ADR-003). The application DB role is the Docker image's
  superuser; read-only transaction is the write barrier, and least-privilege
  role hardening is deferred (ADR-002). No connection pooling. The read-only
  transaction still permits temporary-table operations.

## Recent Progress

- Implemented and committed the schema introspection milestone: `schema.py`,
  tests, and ADR-001.
- Implemented and committed the read-only execution boundary: `query.py`, tests,
  and ADR-002.
- Established the ADR workflow: `adr/README.md`, ADR-001/002/003, and the
  `Architecture Decision Records` section in `AGENTS.md`.
- Implemented and committed the LangChain tool-adapter milestone: added
  `langchain-core`, created `tools.py` and `test_tools.py`, and authored
  ADR-003.
- Removed the obsolete externally-researched reference documents
  `docs/reference/olist-schema.md` and `docs/reference/sql-agent-instructions.md`;
  confirmed no remaining references and
  that source-of-truth guidance in `AGENTS.md` already ranks local
  repository/database evidence above external research.

## Open / Unfinished State

- No LangGraph orchestration, no model/provider integration, no FastAPI service,
  no MCP server, and no frontend integration.
- Least-privilege database hardening is not implemented: the app connects as the
  image's superuser and `default_transaction_read_only` is not set at the role
  level.
- `get_schema()` failures are not surfaced as structured tool errors; only
  `run_sql()` returns structured errors.
- No connection pooling; each `get_schema`/`run_sql` call opens and closes its
  own connection when none is provided.
- The frontend remains the initial scaffold with no integration to the backend.
- Repository root `README.md` and `backend/README.md` are empty.

## Verification

- `RUN_DB_TESTS=1 uv run pytest -q` → 82 passed.
- `RUN_DB_TESTS=1 uv run pytest tests/test_tools.py -q` → 11 passed.
- PostgreSQL reachable; 9 public tables present.
- Olist data loaded: orders 99,441; geolocation 1,000,163.
- `backend/sql/schema.sql` successfully applied and constraints inspected.

## Authoritative References

- `AGENTS.md` — workflow, engineering rules, source-of-truth hierarchy, ADR and CI rules.
- `adr/README.md`, `adr/ADR-001-dynamic-schema-introspection.md`,
  `adr/ADR-002-read-only-sql-execution-boundary.md`,
  `adr/ADR-003-langchain-tool-adapter.md` — finalized decisions and rationale.
- `backend/sql/schema.sql` — physical schema and schema comments.
- `backend/src/ai_sql_analyst/` — implemented backend behavior
  (`db.py`, `schema.py`, `query.py`, `tools.py`, `ingest.py`).
- `backend/tests/` — executable verification.
- `backend/pyproject.toml` — dependencies and project metadata.
- `data/README.md` — dataset provenance and acquisition.
- `docker-compose.yml` — local PostgreSQL service.
- `.env.example` — configuration variables.
- `.github/workflows/ci.yml` — CI behavior (runs `uv run pytest` on backend).

## Session Boundary

- Working tree: only `SESSION_HANDOFF.md` is untracked; there are no staged or
  unstaged changes to tracked files, and nothing has been committed since HEAD
  `522ddf9`.
- Runtime: the Docker daemon is running. The `postgres` service
  (`ai-sql-analyst-postgres-1`, `postgres:16`) is healthy and published on host
  port `5433` (container port 5432). The database `ai_sql_analyst` is reachable
  and holds the nine loaded Olist tables.
- `.env` (gitignored) sets `POSTGRES_PORT=5433`; `docker-compose.yml` publishes
  `${POSTGRES_PORT}` on the host. The data persists in the named volume
  `ai-sql-analyst_postgres_data`.
- `data/raw/` contains the nine Olist CSVs (gitignored) plus `.gitkeep`.
