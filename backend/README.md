# Backend

The backend is a Python 3.12+ service managed with [`uv`](https://docs.astral.sh/uv/).
It provides the framework-neutral database core, the LangGraph agent, the
FastAPI/SSE query endpoint, and the MCP adapter. For the project overview, see the
[root README](../README.md).

## Responsibilities

- Introspect the live PostgreSQL schema.
- Execute a single, untrusted, read-only SQL statement safely.
- Drive a provider-neutral LangGraph tool loop.
- Stream the run and the final answer to the SPA over SSE.
- Expose the same read-only database capability to MCP clients.

## Module map

All modules live in `src/ai_sql_analyst/`.

| Module | Role | Framework-neutral |
| --- | --- | --- |
| `db.py` | Loads configuration from the repo-root `.env` and opens `psycopg` connections. | yes |
| `schema.py` | Introspects `pg_catalog` into typed dataclasses with `to_dict()`. | yes |
| `query.py` | The read-only SQL execution boundary (validation + execution). | yes |
| `tools.py` | LangChain tool adapters (`get_schema`, `run_sql`). | no (LangChain edge) |
| `mcp_server.py` | MCP stdio adapter (`get_schema`, `run_readonly_sql`). | no (MCP edge) |
| `agent.py` | Provider-neutral LangGraph graph and system prompt. | no (LangGraph edge) |
| `model.py` | DeepSeek Responses API model construction (the only provider-specific module). | no (provider edge) |
| `streaming.py` | Pure translation of LangGraph events into application events. | yes |
| `api.py` | FastAPI app, request validation, lifespan, SSE endpoint. | no (HTTP edge) |
| `ingest.py` | Loads the nine Olist CSVs with `COPY FROM STDIN`. | yes |
| `__init__.py` | Package marker. | — |

## Architecture boundaries

```text
query.py / schema.py          framework-neutral database core
        ↓
tools.py / mcp_server.py      adapters (LangChain tools / MCP tools)
        ↓
agent.py / api.py             orchestration + HTTP boundary
```

The arrow shows layering by dependency, not call order. At runtime the agent
calls the tools, and the tools delegate to the core. Every adapter is thin: it
performs no validation, opens no connection of its own, and only forwards the
core's `to_dict()` result.

- `query.py` and `schema.py` have no framework dependencies (no LangChain,
  LangGraph, MCP, or FastAPI).
- `tools.py`, `mcp_server.py`, `agent.py`, `model.py`, and `api.py` are edges.
- Only `model.py` knows DeepSeek; `agent.py` receives a `BaseChatModel`.
- The MCP adapter does not route through LangGraph or FastAPI.

## Requirements

- Python 3.12+
- `uv`
- PostgreSQL 16 (local Docker Compose is provided)

## Environment configuration

Configuration is loaded from the repo-root `.env` (process environment wins).
Start from the template:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `POSTGRES_DB` | Database name. |
| `POSTGRES_USER` | Database user. |
| `POSTGRES_PASSWORD` | Database password. |
| `POSTGRES_HOST` | Database host (defaults to `localhost`). |
| `POSTGRES_PORT` | Database port (defaults to `5432`). |
| `DEEPSEEK_API_KEY` | Key for the DeepSeek Responses API model. |

The same `POSTGRES_*` values drive both the Docker container and the
application.

## PostgreSQL

Start the database:

```bash
docker compose up -d postgres
```

Apply the physical schema once:

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/sql/schema.sql
```

`schema.sql` creates tables, keys, indexes, and column comments. It is not a
migration system and is not intended to be re-applied to an existing schema.

## Ingestion

Place the nine Olist CSVs in `data/raw/` (see [`../data/README.md`](../data/README.md)),
then run ingestion from `backend/`:

```bash
uv run python -m ai_sql_analyst.ingest
```

Ingestion refuses to run if the target tables are missing or already populated.
To truncate and reload:

```bash
uv run python -m ai_sql_analyst.ingest --replace
```

The load runs in a single transaction and prints per-table row counts.

## Running the API

```bash
uv run uvicorn ai_sql_analyst.api:app
```

The API listens on `127.0.0.1:8000` by default.

**Agent/model lifecycle.** At startup, the FastAPI lifespan builds the model and
compiles the LangGraph graph exactly once and stores it on `app.state.agent`;
requests reuse it. `DEEPSEEK_API_KEY` must be set or startup fails. The graph is
stateless, so concurrent requests are independent.

**MCP entry point.** The console script `ai-sql-analyst-mcp` runs the MCP server
over stdio:

```bash
uv run ai-sql-analyst-mcp
```

stdout belongs to the MCP protocol; the server must not write diagnostics there.

## Tests

```bash
uv run pytest -q
RUN_DB_TESTS=1 uv run pytest -q
RUN_MODEL_TESTS=1 RUN_DB_TESTS=1 uv run pytest -q
```

- The default suite is **database-free and model-free**: pure core tests
  (`query`, `schema`), adapter tests (`tools`, `mcp`), graph tests with fake
  models (`agent`), the streaming translator (`streaming`), the API with an
  injected fake agent (`api`), and ingestion helpers.
- `RUN_DB_TESTS=1` enables live PostgreSQL integration tests (schema, query,
  tools, agent, MCP).
- `RUN_MODEL_TESTS=1` enables the opt-in live DeepSeek smoke test; it skips
  itself when no key is available.

Tests live in `tests/` and use pytest. CI runs the default suite.

## SQL safety behavior

`query.py` is the authoritative boundary. It validates query shape, then
PostgreSQL enforces protection:

- read-only transaction (`SET TRANSACTION READ ONLY`);
- single-statement execution via a named server-side cursor;
- statement timeout always set, lock timeout set where meaningful;
- results bounded by an application-owned row limit with a `truncated` flag;
- database failures returned as structured results (`ok: false`, typed error).

Callers control only the SQL text. `max_rows`, `timeout_ms`, and `conn` are
application-owned and are not exposed to the model or to MCP clients.
