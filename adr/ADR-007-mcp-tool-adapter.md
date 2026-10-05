# ADR-007: MCP tool-adapter layer

## Status

Accepted

## Context

The database core (`schema.py`, `query.py`) is framework-neutral by design
(ADR-001, ADR-002), and the LangChain adapter (ADR-003) established the pattern
of wrapping that core at the edge without duplicating or bypassing its safety
logic. ADR-003 explicitly anticipated that "future LangGraph and MCP
integrations are downstream consumers of these tools (or of the same core
functions)".

The project now needs to expose the same analytical capabilities to MCP clients
as a small, additive milestone. That requires deciding where the MCP boundary
lives, what transport it speaks, what the client is allowed to control, how
results and failures cross the boundary, and what dependency this introduces.
The intended dependency direction is:

```text
MCP tool
   ↓
existing application function
   ↓
query.py / schema.py
   ↓
PostgreSQL
```

Constraints in force: `query.py` remains the authoritative SQL trust boundary;
`agent.py`/`api.py` stay provider/HTTP-neutral; no second database, no direct
unrestricted database access, no duplicated SQL validation, no new service
layer, and no public networking or authentication in this milestone.

## Decision

- **MCP is an additional protocol adapter, not the core architecture.** It sits
  beside `tools.py` as a second edge that consumes the same framework-neutral
  functions. It does not replace or reroute the existing
  `React → FastAPI → LangGraph → tools.py → query.py` path.
- **A single module, `backend/src/ai_sql_analyst/mcp_server.py`.** It defines one
  `MCPServer("ai-sql-analyst")` and exposes exactly two tools with
  client-facing names `get_schema` and `run_readonly_sql`. No package or
  subsystem is introduced.
- **The current MCP Python SDK v2 API is used.** The server is built with
  `from mcp.server import MCPServer` and the `@mcp.tool(name=...)` decorator
  factory. The v1 `FastMCP` API is not used.
- **Tools delegate to the existing functions.** `get_schema` returns
  `schema.get_schema().to_dict()`; `run_readonly_sql(sql)` returns
  `query.run_sql(sql).to_dict()`. The module imports only `mcp`, `schema`, and
  `query` -- not LangChain, LangGraph, FastAPI, or `tools.py`.
- **`query.py` remains the trust boundary.** The MCP layer performs no SQL
  validation, opens no connection, and sets no limits; it inherits the read-only
  transaction, single-statement server-side cursor, statement/lock timeouts,
  row cap, and structured errors from `run_sql()`.
- **Only `sql` is client-controlled.** `get_schema` takes no arguments;
  `run_readonly_sql` takes a single `sql: str`. `max_rows`, `timeout_ms`, and
  `conn` are deliberately not exposed, so an MCP client cannot weaken or
  redirect the safety envelope -- the same choice as ADR-003.
- **`.to_dict()` is the serialization contract.** Each tool returns the existing
  `to_dict()` envelope. The return annotation is `dict[str, object]` so the SDK
  publishes an output schema and mirrors the value into `structured_content`
  (as well as JSON text in `content`) without the scalar `{"result": ...}`
  wrapper.
- **Errors are mapped without leaking internals.** `run_sql` already returns
  structured failures (`ok=false`, `error.kind`); these cross MCP as an ordinary
  result, exactly as they reach the LangChain tool. An unexpected failure in
  `get_schema` (for example an unavailable database) propagates and the SDK
  converts it into a sanitized `UnexpectedToolError` (`is_error=true`) with a
  generic message; the raw exception and traceback are logged server-side and
  never sent to the client.
- **Transport is stdio (the SDK default), local/development-facing.**
  `main()` calls `mcp.run()` with no transport argument. A console entry point
  `ai-sql-analyst-mcp` is added so an MCP host has a stable launch command.
  Nothing may write to stdout, which the protocol owns.
- **Dependency: `mcp>=2.3.0`** (plain package, not `mcp[cli]`), added through the
  normal `uv` workflow. `mcp[cli]` is only needed for the optional `mcp dev`
  Inspector and is not required to run or test the server.

## Alternatives considered

- **Give the MCP server direct database access (its own `psycopg` connection and
  SQL).** Rejected: it would duplicate the safety boundary and let the trust
  logic drift from `query.py`. PostgreSQL-in-`query.py` stays the single
  authority.
- **Route MCP through LangGraph or LangChain for reuse.** Rejected: MCP does not
  need an agent, a model, or a chat tool abstraction. Routing through them would
  pull model/provider and orchestration concerns into a protocol edge and couple
  MCP to the agent's evolution.
- **Expose `max_rows`/`timeout_ms`/`conn` (or other execution controls) to MCP
  clients.** Rejected: a client could extend timeouts, lift the row cap, or
  supply its own connection, undermining the envelope. Defaults stay
  application-owned, matching ADR-003.
- **Rewrite the core or the tools as async.** Rejected: the core is synchronous
  and the SDK already runs synchronous tools off-thread. An async rewrite adds
  no capability for a single read-only query.
- **A hosted Streamable HTTP server, with authentication or public networking.**
  Rejected for this milestone: the goal is a local/development integration, and
  hosting/auth are separate concerns with real security weight. stdio is the
  smallest correct transport.
- **Introduce a service layer so both `tools.py` and `mcp_server.py` share it.**
  Rejected: both already share the same functions (`get_schema`, `run_sql`); an
  intermediate layer would add indirection without removing duplication.
- **Keep using the v1 `FastMCP` API.** Rejected: the current SDK is v2 and the
  v1 surface is deprecated; the implementation follows the v2 `MCPServer` API.

## Consequences

- The project gains one runtime dependency, `mcp` (v2), confined to
  `mcp_server.py`. It pulls transitive packages (for example `jsonschema`,
  `mcp-types`, `sse-starlette`, and `pyjwt[crypto]` -> `cryptography`) even
  though this milestone uses only local stdio and no auth; none of them leak
  into the core or the agent path.
- There are now two adapters (`tools.py` for LangChain, `mcp_server.py` for
  MCP) over the same two core functions. They share the `to_dict()` contract and
  the same exposed-argument discipline; a change to the core's `to_dict()` shape
  reaches both automatically.
- The MCP surface is local-trust: any local process able to launch the server
  can run read-only SQL. Write safety still rests on `query.py` and PostgreSQL,
  not on the transport or the client.
- `get_schema()` failures are not surfaced as a structured application error;
  the SDK reports a sanitized generic tool error. This mirrors the limitation
  already documented in ADR-003 (the LangChain adapter lets the same exception
  propagate) and can be revisited if structured schema errors are needed.
- Connections are opened per call with no pooling, consistent with ADR-002.
- The MCP server requires database configuration only; it does not need
  `DEEPSEEK_API_KEY` and never invokes a model.
