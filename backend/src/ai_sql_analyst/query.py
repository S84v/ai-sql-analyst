"""Framework-neutral, read-only SQL execution boundary (see ADR-002).

``run_sql`` executes a single, untrusted, read-only SQL statement and returns a
structured result. Safety is enforced by PostgreSQL (a read-only transaction and
single-statement parsing through a server-side cursor), not by the small lexical
pre-check in this module. Database failures are returned as structured results
rather than raised, so a future LangChain/MCP adapter can present them to a model.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import time
from dataclasses import dataclass

import psycopg
from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode
from psycopg import Connection

from ai_sql_analyst.db import connect

DEFAULT_MAX_ROWS = 500
DEFAULT_TIMEOUT_MS = 5_000

# Server-side cursors reject multi-statement input; a per-connection name is
# reused sequentially (ServerCursor closes any previous cursor with the name).
_CURSOR_NAME = "ai_sql_analyst_query"

_ALLOWED_LEADING = frozenset({"SELECT", "WITH", "VALUES", "TABLE"})

# SQLSTATEs mapped to structured error kinds.
_TIMEOUT_SQLSTATES = frozenset({"57014", "55P03"})  # query_canceled, lock_not_available
_VALIDATION_SQLSTATES = frozenset(
    {"42601", "25006"}  # syntax_error, read_only_sql_transaction
)

# Observability (ADR-010): the OpenTelemetry API only -- no LangChain, LangGraph,
# or FastAPI here, so this module stays framework-neutral. Without a configured
# provider these calls are no-ops, so query.py remains independently testable and
# the MCP path is unaffected. Only low-cardinality metadata is ever recorded;
# the SQL text, parameters, rows, and column values are never attached.
_tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
_sql_executions = _meter.create_counter(
    "olistiq.sql.executions",
    unit="{execution}",
    description="run_sql invocations by outcome.",
)
_sql_duration = _meter.create_histogram(
    "olistiq.sql.duration",
    unit="s",
    description="run_sql execution duration by outcome.",
)


class SqlErrorKind(enum.StrEnum):
    """Classification of a failed ``run_sql`` call."""

    VALIDATION = "validation"
    TIMEOUT = "timeout"
    EXECUTION = "execution"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class SqlError:
    """A structured error returned to the caller instead of an exception."""

    kind: SqlErrorKind
    message: str


@dataclass(frozen=True)
class SqlResult:
    """The outcome of ``run_sql``: rows on success, or a ``SqlError``."""

    ok: bool
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[object, ...], ...] = ()
    row_count: int = 0
    truncated: bool = False
    error: SqlError | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation for tool adapters."""
        return {
            "ok": self.ok,
            "columns": list(self.columns),
            "rows": [[_jsonable(value) for value in row] for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "error": (
                None
                if self.error is None
                else {"kind": self.error.kind.value, "message": self.error.message}
            ),
        }


def _jsonable(value: object) -> object:
    """Coerce PostgreSQL values to JSON-friendly primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _first_significant_token(sql: str) -> str | None:
    """Return the first keyword (or ``"("``) skipping whitespace and comments."""
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            if newline == -1:
                return None
            index = newline + 1
            continue
        if sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            continue
        if char == "(":
            return "("
        end = index
        while end < length and (sql[end].isalnum() or sql[end] == "_"):
            end += 1
        if end == index:
            return char
        return sql[index:end].upper()
    return None


def validate_sql(sql: str) -> SqlError | None:
    """Fast lexical pre-check for read-only shape.

    This is explicitly *not* a proof that arbitrary SQL is safe to run; it only
    rejects empty input and obviously non-query statement classes so the caller
    gets a clear error before touching the database. PostgreSQL remains the
    enforcement layer (read-only transaction and single-statement parsing).
    """
    if not isinstance(sql, str) or not sql.strip():
        return SqlError(SqlErrorKind.VALIDATION, "SQL must be a non-empty string.")
    token = _first_significant_token(sql)
    if token is None:
        return SqlError(SqlErrorKind.VALIDATION, "SQL statement is empty.")
    if token != "(" and token not in _ALLOWED_LEADING:
        return SqlError(
            SqlErrorKind.VALIDATION,
            f"Only read-only queries are allowed; statement starts with '{token}'.",
        )
    return None


def _configure_timeouts(connection: Connection, timeout_ms: int) -> None:
    # timeout_ms is a validated int; statement_timeout is always set. The lock
    # timeout is only meaningful when it is strictly below statement_timeout, so
    # at the minimum (timeout_ms == 1) it is omitted rather than set equal.
    connection.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
    lock_timeout_ms = timeout_ms // 2
    if lock_timeout_ms >= 1:
        connection.execute(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")


def _execute_query(
    connection: Connection, sql: str, max_rows: int, timeout_ms: int
) -> SqlResult:
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        _configure_timeouts(connection, timeout_ms)
        with connection.cursor(name=_CURSOR_NAME) as cur:
            cur.execute(sql)
            columns = tuple(desc.name for desc in (cur.description or ()))
            fetched = cur.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows = tuple(tuple(row) for row in fetched[:max_rows])
    return SqlResult(
        ok=True,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )


def _classify(exc: psycopg.Error) -> SqlError:
    state = getattr(exc, "sqlstate", None)
    message = str(exc).strip()
    if state in _TIMEOUT_SQLSTATES:
        return SqlError(SqlErrorKind.TIMEOUT, message)
    if state in _VALIDATION_SQLSTATES:
        return SqlError(SqlErrorKind.VALIDATION, message)
    return SqlError(SqlErrorKind.EXECUTION, message)


def run_sql(
    sql: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    conn: Connection | None = None,
) -> SqlResult:
    """Execute one read-only SQL statement and return a structured result.

    ``max_rows`` bounds the returned rows (and client-side memory); database
    computation is bounded by ``timeout_ms``, not by ``max_rows``. When ``conn``
    is ``None`` a connection is opened and closed here; a caller-provided
    connection is never closed and must be idle (no open transaction).
    """
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    if timeout_ms < 1:
        raise ValueError("timeout_ms must be >= 1")

    started = time.perf_counter()
    with _tracer.start_as_current_span("run_sql") as span:
        result = _execute_run_sql(
            sql, max_rows=max_rows, timeout_ms=timeout_ms, conn=conn
        )
        _record_run_sql(span, sql, result, time.perf_counter() - started)
        return result


def _execute_run_sql(
    sql: str,
    *,
    max_rows: int,
    timeout_ms: int,
    conn: Connection | None,
) -> SqlResult:
    """Validate then execute one statement; failures stay structured, never raised."""
    error = validate_sql(sql)
    if error is not None:
        return SqlResult(ok=False, error=error)

    connection: Connection | None = None
    try:
        connection = conn if conn is not None else connect()
        return _execute_query(connection, sql, max_rows, timeout_ms)
    except psycopg.Error as exc:
        return SqlResult(ok=False, error=_classify(exc))
    except Exception as exc:  # keep database failures from escaping the boundary
        return SqlResult(ok=False, error=SqlError(SqlErrorKind.UNEXPECTED, str(exc)))
    finally:
        if conn is None and connection is not None:
            connection.close()


def _record_run_sql(span, sql: str, result: SqlResult, duration_s: float) -> None:
    """Record low-cardinality SQL telemetry for one ``run_sql`` call.

    Only structured outcome metadata is attached. The SQL text, parameters, rows,
    and values are never recorded, and raw database error messages are kept out
    of spans (only the coarse ``error.kind`` classification is used).
    """
    if result.ok:
        outcome = "ok"
    elif result.error is not None:
        outcome = result.error.kind.value
    else:
        outcome = SqlErrorKind.UNEXPECTED.value

    span.set_attribute("db.system", "postgresql")
    span.set_attribute("olistiq.sql.outcome", outcome)
    span.set_attribute("olistiq.sql.truncated", bool(result.truncated))
    if result.ok:
        span.set_attribute("olistiq.sql.row_count", result.row_count)
        # Only the four read-only statement classes are recorded; a leading "("
        # (parenthesized query) is not a meaningful operation name.
        operation = _first_significant_token(sql)
        if operation in _ALLOWED_LEADING:
            span.set_attribute("db.operation", operation)
    else:
        span.set_status(Status(StatusCode.ERROR))

    attributes = {"outcome": outcome, "truncated": bool(result.truncated)}
    _sql_executions.add(1, attributes)
    _sql_duration.record(duration_s, attributes)
