"""Tests for the LangChain tool adapters (ADR-003).

Pure tests need no database. Live tests are opt-in via ``RUN_DB_TESTS=1`` and
auto-skip otherwise, matching the convention in ``tests/test_query.py``.
"""

from __future__ import annotations

import os

import pytest
from langchain_core.tools import BaseTool

from ai_sql_analyst import tools as tools_module
from ai_sql_analyst.query import SqlResult
from ai_sql_analyst.schema import ColumnSchema, DatabaseSchema, TableSchema
from ai_sql_analyst.tools import get_schema_tool, run_sql_tool

# ---------------------------------------------------------------------------
# Pure tests (no database)
# ---------------------------------------------------------------------------


def test_tools_are_langchain_tools():
    assert isinstance(get_schema_tool, BaseTool)
    assert isinstance(run_sql_tool, BaseTool)


def test_tool_names():
    assert get_schema_tool.name == "get_schema"
    assert run_sql_tool.name == "run_sql"


def test_tool_descriptions_are_non_empty():
    assert get_schema_tool.description.strip()
    assert run_sql_tool.description.strip()


def test_get_schema_exposes_no_arguments():
    schema = get_schema_tool.args_schema.model_json_schema()
    assert schema.get("properties", {}) == {}
    assert schema.get("required", []) == []


def test_run_sql_exposes_only_sql():
    schema = run_sql_tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert set(properties) == {"sql"}
    assert properties["sql"]["type"] == "string"
    assert schema.get("required", []) == ["sql"]


def test_run_sql_does_not_expose_execution_controls():
    properties = set(
        run_sql_tool.args_schema.model_json_schema().get("properties", {})
    )
    assert properties.isdisjoint({"max_rows", "timeout_ms", "conn"})


def test_get_schema_tool_returns_to_dict(monkeypatch):
    fake = DatabaseSchema(
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
    monkeypatch.setattr(tools_module, "get_schema", lambda: fake)
    assert get_schema_tool.invoke({}) == fake.to_dict()


def test_run_sql_tool_returns_to_dict(monkeypatch):
    fake = SqlResult(
        ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
    )
    monkeypatch.setattr(tools_module, "run_sql", lambda sql: fake)
    assert run_sql_tool.invoke({"sql": "SELECT 1"}) == fake.to_dict()


def test_run_sql_validation_failure_follows_run_sql_path():
    # Validation happens before any connection attempt, so this needs no DB.
    result = run_sql_tool.invoke({"sql": "DROP TABLE x"})
    assert result["ok"] is False
    assert result["error"]["kind"] == "validation"


# ---------------------------------------------------------------------------
# Live-database tests (opt-in; CI has no database)
# ---------------------------------------------------------------------------


def test_live_run_sql_tool():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    result = run_sql_tool.invoke({"sql": "SELECT 1 AS x"})
    assert result["ok"] is True
    assert result["columns"] == ["x"]
    assert result["rows"] == [[1]]


def test_live_get_schema_tool():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    schema = get_schema_tool.invoke({})
    names = {table["name"] for table in schema["tables"]}
    assert {"orders", "customers", "geolocation"} <= names
