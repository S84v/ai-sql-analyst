"""LangChain tool adapters for the framework-neutral database core (ADR-003).

Thin, read-only wrappers exposed to an LLM agent. They only call the existing
``get_schema``/``run_sql`` functions and return their ``to_dict()`` output; all
validation, execution, and safety controls stay inside those functions.
"""

from __future__ import annotations

from langchain_core.tools import tool

from ai_sql_analyst.query import run_sql
from ai_sql_analyst.schema import get_schema


@tool("get_schema")
def get_schema_tool() -> dict:
    """Return the database schema.

    Includes tables, columns, data types, nullability, primary and foreign keys,
    and the documented PostgreSQL comments that describe important caveats.
    """
    return get_schema().to_dict()


@tool("run_sql")
def run_sql_tool(sql: str) -> dict:
    """Execute one read-only SQL statement and return its result.

    `sql` must be a single read-only statement (SELECT, WITH, VALUES, or TABLE).
    Returns a dict of {ok, columns, rows, row_count, truncated, error}; when
    `ok` is false, `error` holds {kind, message}. If `truncated` is true, narrow
    the query (for example with a filter or LIMIT) to see the remaining rows.
    """
    return run_sql(sql).to_dict()
