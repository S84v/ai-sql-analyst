# ADR-002: Read-only SQL execution boundary

## Status

Accepted

## Context

The agent answers natural-language analytical questions by proposing SQL.
That SQL is **untrusted input**: it is produced by a model, not authored by a
trusted developer. The project needs a way to execute it that (a) returns data
for correct read-only analysis, (b) cannot modify data, (c) cannot run away in
cost, and (d) stays framework-neutral so it can later be wrapped by LangChain
and MCP.

Constraints in force: no SQLAlchemy, no connection pooling, no caching, no
semantic views, no new infrastructure; reuse the existing `db.connect()`
boundary. The physical schema (`backend/sql/schema.sql`) and `get_schema()`
already exist.

Design was informed by project-specific observations and official docs:

- With **no bind parameters, Psycopg 3 uses the simple query protocol**, which
  executes multiple statements in one `execute()` call (verified locally:
  `SET application_name = 'x'; SELECT 1` ran both). Extended protocol (with
  parameters) rejects them. The driver therefore cannot be trusted to enforce a
  single statement.
- A **named server-side cursor** (`DECLARE ... CURSOR FOR`) parses via the
  extended protocol and reliably rejects multiple statements (verified locally:
  `SyntaxError: cannot insert multiple commands into a prepared statement`),
  while tolerating a trailing `;`. It also streams results incrementally.
- `SET TRANSACTION READ ONLY` rejects writes and DDL (verified locally:
  `nextval()` and `CREATE TABLE` both failed), and PostgreSQL documents that a
  read-only transaction "cannot alter non-temporary tables".
- `SET LOCAL statement_timeout` cancels long reads (verified locally, including
  during cursor execute/fetch); `lock_timeout` must be strictly less than
  `statement_timeout` to be meaningful.

The guiding principle: **the Python validator cannot prove that arbitrary SQL
is safe. PostgreSQL remains the final enforcement layer for write protection and
statement execution.**

## Decision

`run_sql()` is a framework-neutral function that executes one read-only SQL
statement and returns a structured result.

- **API**: `run_sql(sql, *, max_rows=DEFAULT_MAX_ROWS, timeout_ms=DEFAULT_TIMEOUT_MS, conn=None) -> SqlResult`.
  `SqlResult` is a frozen dataclass (`ok`, `columns`, `rows`, `row_count`,
  `truncated`, `error`) with `to_dict()` for later serialization. Errors are
  `SqlError(kind, message)` with kinds `validation`, `timeout`, `execution`,
  `unexpected`.
- **SQL is untrusted.** A small **lexical pre-check** (`validate_sql`, pure and
  DB-free) rejects empty/comment-only input and any leading statement class
  other than `SELECT`, `WITH`, `VALUES`, `TABLE`, or a parenthesised query. It
  exists to give fast, clear rejection of obvious writes and is explicitly *not*
  a safety proof.
- **PostgreSQL enforces read-only execution.** Each call runs in its own
  transaction that begins with `SET TRANSACTION READ ONLY`, so writes, DDL, and
  write-performing CTEs/functions are rejected by the server regardless of what
  the validator allowed.
- **Multiple statements must be rejected.** This is enforced by executing
  through a **named server-side cursor**, which parses via the extended protocol
  and rejects multi-statement input server-side. No hand-written SQL lexer tries
  to split statements.
- **Bounded execution.** Each invocation sets `SET LOCAL statement_timeout` and
  `SET LOCAL lock_timeout` (strictly less than the statement timeout). Because
  multiple statements are rejected, a query cannot append `SET statement_timeout
  = 0` to disable the bound.
- **Bounded results.** The cursor fetches `max_rows + 1` rows; if an extra row
  exists, `truncated` is set and the extra row dropped. This bounds client memory.
  **Database computation is bounded by the timeout, not by `max_rows`.**
- **Connection ownership** mirrors `get_schema()`: when `conn is None`,
  `run_sql` opens and closes its own connection via `db.connect()`; a
  caller-provided connection is never closed and is required to be idle (no
  open transaction).
- **Failures are structured tool results.** SQL/database problems (syntax,
  read-only violation, cancellation, other `psycopg.Error`) are returned inside
  `SqlResult`, not raised, so the future agent-tool adapter can present them to
  the model without SQL/database exceptions escaping the boundary.

## Alternatives considered

- **Regex/denylist as the primary security mechanism.** Rejected: it cannot
  reliably distinguish safe SQL from writes, is trivially bypassed by CTEs,
  functions, comments and quoting, and would create a false sense of safety.
  PostgreSQL is the authority instead.
- **Relying on Psycopg's protocol behavior to reject multiple statements.**
  Rejected: no-parameter `execute()` uses the simple query protocol and runs
  multiple statements (confirmed locally). The server-side cursor is used
  precisely because it forces the extended-protocol single-statement path.
- **Wrapping arbitrary SQL in a `LIMIT` subquery.** Rejected: it breaks valid
  forms such as `VALUES`, `TABLE`, and parenthesised queries, and can alter
  semantics.
- **Fetching the entire result then slicing.** Rejected: it would materialize up
  to ~1M rows (e.g. `geolocation`) client-side.
- **`sqlparse`/`pglast` solely for this milestone.** Rejected: an extra
  dependency and a partial parser that still would not make the validator
  authoritative; PostgreSQL already parses the statement.
- **Connection pooling.** Rejected/deferred: unnecessary at this scale and
  against the milestone constraints.
- **Role-level database hardening now.** Deferred deliberately: a dedicated
  non-superuser, read-only role and `default_transaction_read_only` are the
  right defense-in-depth follow-up, but out of scope for this milestone. The
  read-only transaction is the immediate barrier.
- **Returning results via exceptions instead of a structured envelope.**
  Rejected: a structured result is friendlier for the eventual LLM tool adapter.

## Consequences

- The write boundary is enforced by PostgreSQL, not by Python; validation only
  improves the error experience.
- The design is honest about its limits: a read-only transaction blocks changes
  to non-temporary tables but does not block temporary-table writes, does not
  restrict which rows/columns a privileged role may read, and does not prevent
  expensive read-only queries — those are bounded only by the timeout.
- It does not make an unrestricted database role inherently safe. The current
  local `ai_sql_analyst` role is the image's superuser, so least-privilege role
  hardening remains a recommended later improvement.
- Execution requires a live database, so live tests are opt-in; the pure
  validator and result serialization are covered by database-free unit tests.
- Later LangChain/MCP adapters should only wrap `run_sql().to_dict()`; they must
  not bypass this boundary.
