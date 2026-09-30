"""Framework-neutral PostgreSQL schema introspection for AI SQL Analyst.

``get_schema`` reads the live database catalog and returns a deterministic
``DatabaseSchema``. It depends only on psycopg and the existing ``db``
connection boundary, so it can later be wrapped by LangChain or MCP without
those frameworks leaking into this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection

from ai_sql_analyst.db import connect


@dataclass(frozen=True)
class ColumnSchema:
    """One table column as exposed to an SQL-generating model."""

    name: str
    data_type: str
    nullable: bool
    comment: str | None


@dataclass(frozen=True)
class ForeignKeySchema:
    """A foreign-key relationship between two tables."""

    columns: tuple[str, ...]
    references_table: str
    references_columns: tuple[str, ...]


@dataclass(frozen=True)
class TableSchema:
    """A table with the metadata an SQL generator needs."""

    name: str
    comment: str | None
    columns: tuple[ColumnSchema, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[ForeignKeySchema, ...]
    checks: tuple[str, ...]


@dataclass(frozen=True)
class DatabaseSchema:
    """The introspected schema, ordered deterministically by table name."""

    tables: tuple[TableSchema, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation for later tool wrapping."""
        return {
            "tables": [
                {
                    "name": table.name,
                    "comment": table.comment,
                    "primary_key": list(table.primary_key),
                    "columns": [
                        {
                            "name": column.name,
                            "data_type": column.data_type,
                            "nullable": column.nullable,
                            "comment": column.comment,
                        }
                        for column in table.columns
                    ],
                    "foreign_keys": [
                        {
                            "columns": list(fk.columns),
                            "references_table": fk.references_table,
                            "references_columns": list(fk.references_columns),
                        }
                        for fk in table.foreign_keys
                    ],
                    "checks": list(table.checks),
                }
                for table in self.tables
            ]
        }


# Raw catalog rows, keyed by table OID so PK/FK attribute numbers can be
# resolved to column names in Python. OIDs never appear in the output.
@dataclass(frozen=True)
class Catalog:
    """Raw pg_catalog rows used to build a ``DatabaseSchema``."""

    # (oid, name, comment)
    tables: tuple[tuple[int, str, str | None], ...]
    # (oid, attnum, name, data_type, nullable, comment)
    columns: tuple[tuple[int, int, str, str, bool, str | None], ...]
    # (oid, attnums)
    primary_keys: tuple[tuple[int, tuple[int, ...]], ...]
    # (oid, local attnums, referenced oid, referenced attnums)
    foreign_keys: tuple[tuple[int, tuple[int, ...], int, tuple[int, ...]], ...]
    # (oid, check definition)
    checks: tuple[tuple[int, str], ...]


_TABLES_SQL = """
SELECT c.oid, c.relname, obj_description(c.oid, 'pg_class')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""

_COLUMNS_SQL = """
SELECT a.attrelid, a.attnum, a.attname,
       format_type(a.atttypid, a.atttypmod),
       NOT a.attnotnull,
       col_description(a.attrelid, a.attnum)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attrelid, a.attnum
"""

_PRIMARY_KEYS_SQL = """
SELECT con.conrelid, con.conkey
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE con.contype = 'p' AND n.nspname = 'public' AND c.relkind = 'r'
ORDER BY con.conrelid
"""

_FOREIGN_KEYS_SQL = """
SELECT con.conrelid, con.conkey, con.confrelid, con.confkey
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class rc ON rc.oid = con.confrelid
JOIN pg_namespace rn ON rn.oid = rc.relnamespace
WHERE con.contype = 'f' AND n.nspname = 'public' AND c.relkind = 'r'
  AND rn.nspname = 'public'
ORDER BY con.conrelid, con.conname
"""

_CHECKS_SQL = """
SELECT con.conrelid, pg_get_constraintdef(con.oid)
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE con.contype = 'c' AND n.nspname = 'public' AND c.relkind = 'r'
ORDER BY con.conrelid, con.conname
"""


def fetch_catalog(conn: Connection) -> Catalog:
    """Read the raw catalog rows for the ``public`` schema."""
    with conn.cursor() as cur:
        cur.execute(_TABLES_SQL)
        tables = tuple(
            (int(oid), name, comment) for oid, name, comment in cur.fetchall()
        )
        cur.execute(_COLUMNS_SQL)
        columns = tuple(
            (int(oid), int(attnum), name, data_type, bool(nullable), comment)
            for oid, attnum, name, data_type, nullable, comment in cur.fetchall()
        )
        cur.execute(_PRIMARY_KEYS_SQL)
        primary_keys = tuple(
            (int(oid), tuple(attnums)) for oid, attnums in cur.fetchall()
        )
        cur.execute(_FOREIGN_KEYS_SQL)
        foreign_keys = tuple(
            (int(oid), tuple(local), int(ref_oid), tuple(ref_attnums))
            for oid, local, ref_oid, ref_attnums in cur.fetchall()
        )
        cur.execute(_CHECKS_SQL)
        checks = tuple(
            (int(oid), definition) for oid, definition in cur.fetchall()
        )
    return Catalog(tables, columns, primary_keys, foreign_keys, checks)


def build_schema(catalog: Catalog) -> DatabaseSchema:
    """Transform raw catalog rows into a deterministic, typed schema.

    Pure function: it performs no database access and is unit-testable with a
    hand-built ``Catalog``.
    """
    table_names = {oid: name for oid, name, _ in catalog.tables}

    columns_by_table: dict[int, list[tuple[int, str, str, bool, str | None]]] = {}
    column_names: dict[tuple[int, int], str] = {}
    for oid, attnum, name, data_type, nullable, comment in catalog.columns:
        columns_by_table.setdefault(oid, []).append(
            (attnum, name, data_type, nullable, comment)
        )
        column_names[(oid, attnum)] = name

    primary_keys = {
        oid: tuple(column_names[(oid, attnum)] for attnum in attnums)
        for oid, attnums in catalog.primary_keys
    }

    foreign_keys_by_table: dict[int, list[ForeignKeySchema]] = {}
    for oid, local, ref_oid, ref_attnums in catalog.foreign_keys:
        foreign_keys_by_table.setdefault(oid, []).append(
            ForeignKeySchema(
                columns=tuple(column_names[(oid, attnum)] for attnum in local),
                references_table=table_names[ref_oid],
                references_columns=tuple(
                    column_names[(ref_oid, attnum)] for attnum in ref_attnums
                ),
            )
        )

    checks_by_table: dict[int, list[str]] = {}
    for oid, definition in catalog.checks:
        checks_by_table.setdefault(oid, []).append(definition)

    tables = []
    for oid, name, comment in catalog.tables:
        columns = sorted(columns_by_table.get(oid, []), key=lambda col: col[0])
        tables.append(
            TableSchema(
                name=name,
                comment=comment,
                columns=tuple(
                    ColumnSchema(
                        name=col_name,
                        data_type=data_type,
                        nullable=nullable,
                        comment=col_comment,
                    )
                    for _, col_name, data_type, nullable, col_comment in columns
                ),
                primary_key=primary_keys.get(oid, ()),
                foreign_keys=tuple(
                    sorted(
                        foreign_keys_by_table.get(oid, []),
                        key=lambda fk: (fk.references_table, fk.columns),
                    )
                ),
                checks=tuple(sorted(checks_by_table.get(oid, []))),
            )
        )

    tables.sort(key=lambda table: table.name)
    return DatabaseSchema(tables=tuple(tables))


def get_schema(conn: Connection | None = None) -> DatabaseSchema:
    """Return the schema, opening and closing a connection when none is given."""
    if conn is not None:
        return build_schema(fetch_catalog(conn))
    with connect() as connection:
        return build_schema(fetch_catalog(connection))
