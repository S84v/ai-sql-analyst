"""Tests for the read-only SQL execution boundary (ADR-002).

Pure tests need no database. Live tests are opt-in via ``RUN_DB_TESTS=1`` and
auto-skip otherwise, matching the pattern in ``tests/test_schema.py`` so the
default CI run stays database-free.
"""

from __future__ import annotations

import datetime
import decimal
import json
import os

import pytest
import psycopg

from ai_sql_analyst import query as query_module
from ai_sql_analyst.db import connect
from ai_sql_analyst.query import (
    SqlError,
    SqlErrorKind,
    SqlResult,
    run_sql,
    validate_sql,
)

# ---------------------------------------------------------------------------
# Pure tests (no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select 1",
        "  SELECT 1",
        "-- leading comment\nSELECT 1",
        "/* leading comment */ SELECT 1",
        "/* outer /* nested */ still comment */ SELECT 1",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "VALUES (1), (2)",
        "TABLE orders",
        "(SELECT 1)",
    ],
)
def test_validate_sql_accepts_read_only_forms(sql):
    assert validate_sql(sql) is None


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "-- only a comment",
        "/* only a comment */",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t (id int)",
        "ALTER TABLE t ADD COLUMN c int",
        "TRUNCATE t",
        "COPY t FROM STDIN",
        "SET statement_timeout = 0",
        "EXPLAIN SELECT 1",
        "GRANT SELECT ON t TO PUBLIC",
        "CALL some_procedure()",
    ],
)
def test_validate_sql_rejects_other_statements(sql):
    error = validate_sql(sql)
    assert error is not None
    assert error.kind is SqlErrorKind.VALIDATION


def test_to_dict_success_is_json_serializable():
    result = SqlResult(
        ok=True, columns=("x",), rows=((1,),), row_count=1, truncated=False
    )
    payload = result.to_dict()
    assert payload == {
        "ok": True,
        "columns": ["x"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "error": None,
    }
    assert json.loads(json.dumps(payload)) == payload


def test_to_dict_coerces_non_json_types():
    result = SqlResult(
        ok=True,
        columns=("d", "n"),
        rows=((datetime.date(2020, 1, 1), decimal.Decimal("1.50")),),
        row_count=1,
    )
    payload = result.to_dict()
    assert payload["rows"] == [["2020-01-01", "1.50"]]
    assert json.loads(json.dumps(payload)) == payload


def test_to_dict_error_envelope():
    result = SqlResult(ok=False, error=SqlError(SqlErrorKind.VALIDATION, "nope"))
    payload = result.to_dict()
    assert payload["ok"] is False
    assert payload["error"] == {"kind": "validation", "message": "nope"}
    assert payload["rows"] == []
    assert payload["row_count"] == 0


@pytest.mark.parametrize("kwargs", [{"max_rows": 0}, {"timeout_ms": 0}])
def test_run_sql_rejects_bad_limits_without_connecting(kwargs):
    with pytest.raises(ValueError):
        run_sql("SELECT 1", **kwargs)


def test_run_sql_reports_validation_error_without_connecting():
    result = run_sql("DROP TABLE customers")
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is SqlErrorKind.VALIDATION


def test_run_sql_returns_structured_error_on_connection_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(query_module, "connect", fail)
    result = run_sql("SELECT 1")
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind in {SqlErrorKind.EXECUTION, SqlErrorKind.UNEXPECTED}


# ---------------------------------------------------------------------------
# Live-database tests (opt-in; CI has no database)
# ---------------------------------------------------------------------------


@pytest.fixture
def live_connection():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def test_live_simple_select(live_connection):
    result = run_sql("SELECT 1 AS x", conn=live_connection)
    assert result.ok is True
    assert result.columns == ("x",)
    assert result.rows == ((1,),)
    assert result.row_count == 1
    assert result.truncated is False
    assert result.error is None


def test_live_selects_from_real_table(live_connection):
    result = run_sql(
        "SELECT order_id FROM orders ORDER BY order_id LIMIT 3", conn=live_connection
    )
    assert result.ok is True
    assert result.columns == ("order_id",)
    assert result.row_count == 3


def test_live_truncation_sets_flag(live_connection):
    result = run_sql("SELECT * FROM geolocation", max_rows=5, conn=live_connection)
    assert result.ok is True
    assert result.row_count == 5
    assert result.truncated is True


def test_live_truncation_boundary(live_connection):
    sql = "SELECT * FROM (VALUES (1), (2), (3)) AS t(x)"

    exact = run_sql(sql, max_rows=3, conn=live_connection)
    assert exact.ok is True
    assert exact.row_count == 3
    assert exact.truncated is False

    limited = run_sql(sql, max_rows=2, conn=live_connection)
    assert limited.ok is True
    assert limited.row_count == 2
    assert limited.truncated is True


def test_live_multiple_statements_rejected(live_connection):
    result = run_sql("SELECT 1; SELECT 2", conn=live_connection)
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is SqlErrorKind.VALIDATION


def test_live_leading_write_rejected(live_connection):
    result = run_sql(
        "INSERT INTO product_category_translation "
        "(product_category_name, product_category_name_english) "
        "VALUES ('probe', 'probe')",
        conn=live_connection,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is SqlErrorKind.VALIDATION


def test_live_data_modifying_cte_is_blocked(live_connection):
    before = run_sql(
        "SELECT count(*) FROM product_category_translation", conn=live_connection
    ).rows[0][0]
    result = run_sql(
        "WITH x AS ("
        "INSERT INTO product_category_translation "
        "(product_category_name, product_category_name_english) "
        "VALUES ('zzz_probe', 'zzz_probe') RETURNING product_category_name"
        ") SELECT * FROM x",
        conn=live_connection,
    )
    after = run_sql(
        "SELECT count(*) FROM product_category_translation", conn=live_connection
    ).rows[0][0]
    assert result.ok is False
    assert result.error is not None
    # PostgreSQL rejects the data-modifying CTE at DECLARE time (SQLSTATE
    # 0A000, "DECLARE CURSOR must not contain data-modifying statements in
    # WITH"), which maps to EXECUTION; VALIDATION is accepted too.
    assert result.error.kind in {SqlErrorKind.VALIDATION, SqlErrorKind.EXECUTION}
    assert before == after


def test_live_timeout_is_reported(live_connection):
    result = run_sql("SELECT pg_sleep(3)", timeout_ms=200, conn=live_connection)
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is SqlErrorKind.TIMEOUT


def test_live_read_only_blocks_nextval(live_connection):
    result = run_sql(
        "SELECT nextval(pg_get_serial_sequence('geolocation', 'geolocation_id'))",
        conn=live_connection,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.kind is SqlErrorKind.VALIDATION


def test_live_caller_connection_is_not_closed(live_connection):
    run_sql("SELECT 1", conn=live_connection)
    assert not live_connection.closed
    assert live_connection.execute("SELECT 1").fetchone() == (1,)


def test_live_result_is_serializable(live_connection):
    result = run_sql("SELECT 1 AS x", conn=live_connection)
    assert json.loads(json.dumps(result.to_dict()))
