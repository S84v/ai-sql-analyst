"""Boundary tests for the MCP stdio adapter (ADR-007).

These tests exercise the MCP server through the SDK's in-memory client
(``mcp.Client``): they verify the exposed tools, their argument schemas, the
result envelopes, and the error mapping at the MCP boundary -- not the Python
functions underneath. The existing functions are mocked only where the test is
about delegation, always preserving the adapter's real call direction. Live
tests are opt-in via ``RUN_DB_TESTS=1``, matching ``tests/test_tools.py``.
"""

from __future__ import annotations

import os

import pytest
from mcp import Client

from ai_sql_analyst import mcp_server
from ai_sql_analyst.query import SqlResult
from ai_sql_analyst.schema import ColumnSchema, DatabaseSchema, TableSchema


class _Tools(dict):
    """Tool listing keyed by name, for concise lookups inside a client block."""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _list_tools(client: Client) -> _Tools:
    listing = await client.list_tools()
    return _Tools({tool.name: tool for tool in listing.tools})


def _content_text(result) -> str:
    return " ".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


def _fake_schema() -> DatabaseSchema:
    return DatabaseSchema(
        tables=(
            TableSchema(
                name="t",
                comment=None,
                columns=(
                    ColumnSchema(
                        name="c", data_type="text", nullable=False, comment=None
                    ),
                ),
                primary_key=("c",),
                foreign_keys=(),
                checks=(),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_server_exposes_exactly_two_tools():
    async with Client(mcp_server.mcp) as client:
        tools = await _list_tools(client)
    assert set(tools) == {"get_schema", "run_readonly_sql"}


@pytest.mark.anyio
async def test_get_schema_accepts_no_arguments():
    async with Client(mcp_server.mcp) as client:
        tools = await _list_tools(client)
    assert tools["get_schema"].input_schema.get("properties", {}) == {}
    assert tools["get_schema"].input_schema.get("required", []) == []


@pytest.mark.anyio
async def test_run_readonly_sql_accepts_only_sql():
    async with Client(mcp_server.mcp) as client:
        tools = await _list_tools(client)
    properties = tools["run_readonly_sql"].input_schema.get("properties", {})
    assert set(properties) == {"sql"}
    assert properties["sql"]["type"] == "string"
    assert tools["run_readonly_sql"].input_schema.get("required", []) == ["sql"]


@pytest.mark.anyio
async def test_run_readonly_sql_does_not_expose_execution_controls():
    async with Client(mcp_server.mcp) as client:
        tools = await _list_tools(client)
    properties = set(tools["run_readonly_sql"].input_schema.get("properties", {}))
    assert properties.isdisjoint({"max_rows", "timeout_ms", "conn"})


# ---------------------------------------------------------------------------
# Delegation and result shape
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_schema_returns_existing_to_dict(monkeypatch):
    fake = _fake_schema()
    monkeypatch.setattr(mcp_server, "get_schema", lambda: fake)
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("get_schema", {})
    assert result.is_error is False
    assert result.structured_content == fake.to_dict()


@pytest.mark.anyio
async def test_run_readonly_sql_returns_existing_to_dict(monkeypatch):
    fake = SqlResult(
        ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
    )
    monkeypatch.setattr(mcp_server, "run_sql", lambda sql: fake)
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("run_readonly_sql", {"sql": "SELECT 1"})
    assert result.is_error is False
    assert result.structured_content == fake.to_dict()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rejected_sql_reaches_existing_validation_boundary():
    # No mock and no database: the real run_sql lexical pre-check rejects it
    # before any connection attempt, and the structured error crosses MCP intact.
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("run_readonly_sql", {"sql": "DROP TABLE x"})
    assert result.is_error is False
    assert result.structured_content["ok"] is False
    assert result.structured_content["error"]["kind"] == "validation"


@pytest.mark.anyio
async def test_schema_failure_does_not_leak_exception_text(monkeypatch):
    def boom():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(mcp_server, "get_schema", boom)
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("get_schema", {})
    assert result.is_error is True
    assert "secret internal detail" not in _content_text(result)


# ---------------------------------------------------------------------------
# Live database (opt-in; CI has no database)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_live_get_schema_through_mcp():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("get_schema", {})
    names = {table["name"] for table in result.structured_content["tables"]}
    assert {"orders", "customers", "geolocation"} <= names


@pytest.mark.anyio
async def test_live_run_readonly_sql_through_mcp():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    async with Client(mcp_server.mcp) as client:
        result = await client.call_tool("run_readonly_sql", {"sql": "SELECT 1 AS x"})
    assert result.structured_content["ok"] is True
    assert result.structured_content["columns"] == ["x"]
    assert result.structured_content["rows"] == [[1]]
