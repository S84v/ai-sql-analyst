# ADR-001: Dynamic, catalog-based schema introspection

## Status

Accepted

## Context

The SQL agent needs to know the database structure in order to generate correct
analytical SQL. The physical schema lives in `backend/sql/schema.sql`, and much
of its important meaning lives in PostgreSQL `COMMENT ON` statements — the
empirical caveats about `customer_id` vs `customer_unique_id`, non-unique
`review_id`, `order_items`/`order_payments` fan-out, geolocation duplication,
incomplete category translations, and naive timestamps.

Two forces shaped the design:

1. The schema and its comments are the source of truth and may evolve; any
   representation kept in Python could drift from the database.
2. The eventual consumption path (LangChain, and possibly MCP) was not yet
   chosen, so the introspection layer had to stay framework-neutral.

## Decision

- **Introspect the live database dynamically rather than hard-coding a schema in
  Python.** The implementation reads `pg_catalog` for tables, columns, data
  types, nullability, primary keys, foreign keys, and CHECK constraints, plus
  table/column comments. Because comments are read from the catalog, the
  documented caveats are preserved automatically without duplicating them.
- **The physical schema is the source of truth.** Introspection reflects
  whatever is actually deployed in the `public` schema; there is no second,
  hand-maintained schema model to keep in sync.
- **Separate catalog extraction from the pure build/serialization layer.**
  `fetch_catalog()` is the only database-bound step and returns an immutable
  `Catalog`; `build_schema()` is a pure function that maps the catalog to typed
  dataclasses and resolves composite PK/FK attribute numbers. `get_schema()` is
  a thin orchestrator that reuses the existing `db.connect()` boundary. This
  split keeps the representation unit-testable without a live database.
- **Do not couple `schema.py` to LangChain, LangGraph, MCP, or FastAPI.** The
  output is plain dataclasses with a `to_dict()` serialization hook; framework
  adapters are added later at the edges without changing this core.
- **Keep the public output minimal.** Tables, columns, types, nullability,
  comments, primary keys, foreign keys, and CHECK definitions only — no indexes,
  row counts, statistics, ownership, constraint names, or storage details.

## Alternatives considered

- **Hard-coding the schema in Python.** Rejected: it would drift from the
  database and duplicate the comment-based caveats.
- **`information_schema` instead of `pg_catalog`.** Rejected: `information_schema`
  does not expose object comments and does not give exact type spellings as
  cleanly as `format_type()`.
- **A SQLAlchemy (or similar) inspector.** Rejected: an unnecessary heavy
  dependency for a read-only introspection need, and it would couple the layer
  to an ORM abstraction the project does not use.
- **Merging `fetch_catalog` and `build_schema` into one database function.**
  Rejected: it would make the transformation untestable without a live database.
- **Returning a dict directly instead of typed dataclasses.** Rejected: a typed
  core is easier to test and keeps the serialized wire shape an explicit choice;
  `to_dict()` provides the JSON form when needed.
- **Adding LangChain/MCP wrappers now.** Rejected: the frameworks are not yet
  chosen, and coupling early would constrain them.

## Consequences

- The introspected schema always matches the deployed database and preserves
  comment semantics with no duplication.
- Catalog queries require a live PostgreSQL connection; the pure `build_schema`
  transformation, however, is fully covered by database-free unit tests, and an
  opt-in integration test exercises the catalog queries.
- The `Catalog` intermediate adds a small, deliberate layer of indirection in
  exchange for testability and immutability.
- Consumers (LangChain, MCP) remain decoupled; adding them later should require
  only thin adapters over `get_schema().to_dict()`.
