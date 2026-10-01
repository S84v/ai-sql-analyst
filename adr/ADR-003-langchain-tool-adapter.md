# ADR-003: LangChain tool-adapter layer

## Status

Accepted

## Context

The database core (`schema.py`, `query.py`) is deliberately framework-neutral
(ADR-001, ADR-002): `get_schema()` returns typed dataclasses with a `to_dict()`
hook, and `run_sql()` executes a single read-only statement and returns a
structured `SqlResult`. Both ADRs anticipated that an LLM agent would consume
this core later and required that framework adapters stay at the edge without
bypassing the safety boundary.

The next milestone needs to expose the two existing capabilities to an LLM as
callable tools. That means introducing a framework dependency for the first
time and fixing where the adapter lives, what the model is allowed to control,
and how results cross the boundary.

## Decision

- **Introduce a thin framework edge around the framework-neutral core.** A new
  module, `backend/src/ai_sql_analyst/tools.py`, defines the agent-facing tools.
  The core modules are unchanged.
- **`langchain-core` is the minimal dependency.** It provides the `@tool`
  decorator and `BaseTool`/`StructuredTool`. The full `langchain` package
  re-exports these from `langchain-core`; it also pulls in agent and model
  machinery that this milestone does not use, so only `langchain-core` is added.
- **`tools.py` is the adapter boundary.** It exposes exactly two module-level
  tools with model-facing names `get_schema` and `run_sql` (Python identifiers
  `get_schema_tool` and `run_sql_tool`). It contains no validation, execution,
  connection, or serialization logic of its own.
- **`.to_dict()` is the serialization contract.** `get_schema_tool` returns
  `get_schema().to_dict()`; `run_sql_tool` returns `run_sql(sql).to_dict()`.
  The wire shape is owned by the core dataclasses, so the adapter cannot drift
  from it.
- **Only `sql` is model-controlled.** `run_sql_tool` accepts a single `sql: str`
  argument; `get_schema_tool` accepts no arguments.
- **Execution controls remain application-owned.** `max_rows`, `timeout_ms`, and
  `conn` are deliberately not exposed to the model; the tools call `run_sql`
  with its established defaults so the model cannot weaken or redirect the
  safety envelope.
- **Tools are synchronous for this milestone.** No async is required for a
  single read-only query.
- **Tools must delegate to the existing trusted database functions.** They do
  not re-implement or bypass `get_schema`/`run_sql`, and therefore inherit the
  read-only transaction, single-statement enforcement, and structured errors
  from ADR-002.

## Alternatives considered

- **Depend on the full `langchain` package.** Rejected: it re-exports the same
  tools from `langchain-core` but adds agent/model surface this milestone does
  not use. `langchain-core` is the smallest package that provides the tools.
- **Put the `@tool` decorators in `schema.py`/`query.py`.** Rejected: it would
  couple the framework-neutral core to LangChain, violating ADR-001/ADR-002.
- **Expose execution controls (`max_rows`, `timeout_ms`, `conn`) to the model.**
  Rejected: it would let the model weaken row bounds, extend timeouts, or supply
  its own connection, undermining the safety boundary.
- **Define custom `BaseTool` subclasses.** Rejected: the `@tool` decorator
  already produces a `StructuredTool` with the required schema; subclasses would
  add unused abstraction.
- **Introduce another result abstraction.** Rejected: the existing `to_dict()`
  envelope is the established serialization contract; a second layer would
  duplicate it.

## Consequences

- The project gains one runtime dependency (`langchain-core`), confined to the
  edge module; the database core remains framework-neutral and independently
  unit-testable.
- The model can call exactly two tools and cannot influence limits, timeouts, or
  connections.
- The adapter is optional and replaceable: removing or swapping the framework
  affects only `tools.py`.
- `get_schema()` can raise on an unavailable database; the adapter stays thin,
  and tool-exception handling is deferred to the eventual agent layer.
- Future LangGraph and MCP integrations are downstream consumers of these tools
  (or of the same core functions); nothing in this ADR presumes an agent graph
  or model provider exists yet.
