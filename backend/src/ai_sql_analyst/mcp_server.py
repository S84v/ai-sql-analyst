"""MCP stdio adapter for the framework-neutral database core (ADR-007).

Exposes the existing read-only capabilities as MCP tools, mirroring the
LangChain adapter in ``tools.py`` (ADR-003) while depending only on the
framework-neutral functions in ``query.py``/``schema.py``. Each tool delegates
directly; all validation, execution, connection, and serialization logic stays
inside those functions, so ``query.py`` remains the SQL trust boundary.

MCP is an additional protocol adapter, not part of the LangGraph/FastAPI agent
path. This module never imports LangChain and never opens a database connection
itself. The transport is stdio (the SDK default), so nothing here may write to
stdout -- the protocol stream owns stdout, and diagnostics belong on stderr.
"""

from __future__ import annotations

from mcp.server import MCPServer

from ai_sql_analyst.query import run_sql
from ai_sql_analyst.schema import get_schema

mcp = MCPServer("ai-sql-analyst")


@mcp.tool(name="get_schema")
def get_schema_tool() -> dict[str, object]:
    """Return the database schema.

    Includes tables, columns, data types, nullability, primary and foreign keys,
    and the documented PostgreSQL comments that describe important caveats.
    """
    return get_schema().to_dict()


@mcp.tool(name="run_readonly_sql")
def run_readonly_sql_tool(sql: str) -> dict[str, object]:
    """Execute one read-only SQL statement and return its result.

    `sql` must be a single read-only statement (SELECT, WITH, VALUES, or TABLE).
    Returns a dict of {ok, columns, rows, row_count, truncated, error}; when
    `ok` is false, `error` holds {kind, message}. If `truncated` is true, narrow
    the query (for example with a filter or LIMIT) to see the remaining rows.
    """
    return run_sql(sql).to_dict()


def main() -> None:
    """Run the MCP server over stdio (the SDK default transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
