"""Load the nine Olist CSV files into PostgreSQL using COPY FROM STDIN.

The CSVs are parsed by PostgreSQL's own CSV reader, streamed from disk in
binary chunks. This preserves values verbatim and correctly handles quoted
fields that contain embedded newlines (present in the reviews file).

Run from the backend directory:

    uv run python -m ai_sql_analyst.ingest
    uv run python -m ai_sql_analyst.ingest --replace
    uv run python -m ai_sql_analyst.ingest --raw-dir /path/to/data/raw
"""

from __future__ import annotations

import argparse
import codecs
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from psycopg import Connection, sql

from ai_sql_analyst.db import REPO_ROOT, connect

CHUNK_SIZE = 1024 * 1024
BOM = codecs.BOM_UTF8


@dataclass(frozen=True)
class TableLoad:
    """One CSV file and the table/columns it loads into."""

    table: str
    filename: str
    columns: tuple[str, ...]


# Order matters: referenced (parent) tables load before their dependents.
LOAD_SPECS: tuple[TableLoad, ...] = (
    TableLoad(
        "customers",
        "olist_customers_dataset.csv",
        (
            "customer_id",
            "customer_unique_id",
            "customer_zip_code_prefix",
            "customer_city",
            "customer_state",
        ),
    ),
    TableLoad(
        "sellers",
        "olist_sellers_dataset.csv",
        (
            "seller_id",
            "seller_zip_code_prefix",
            "seller_city",
            "seller_state",
        ),
    ),
    TableLoad(
        "products",
        "olist_products_dataset.csv",
        (
            "product_id",
            "product_category_name",
            "product_name_lenght",
            "product_description_lenght",
            "product_photos_qty",
            "product_weight_g",
            "product_length_cm",
            "product_height_cm",
            "product_width_cm",
        ),
    ),
    TableLoad(
        "product_category_translation",
        "product_category_name_translation.csv",
        (
            "product_category_name",
            "product_category_name_english",
        ),
    ),
    TableLoad(
        "geolocation",
        "olist_geolocation_dataset.csv",
        (
            "geolocation_zip_code_prefix",
            "geolocation_lat",
            "geolocation_lng",
            "geolocation_city",
            "geolocation_state",
        ),
    ),
    TableLoad(
        "orders",
        "olist_orders_dataset.csv",
        (
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
    ),
    TableLoad(
        "order_items",
        "olist_order_items_dataset.csv",
        (
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ),
    ),
    TableLoad(
        "order_payments",
        "olist_order_payments_dataset.csv",
        (
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ),
    ),
    TableLoad(
        "order_reviews",
        "olist_order_reviews_dataset.csv",
        (
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ),
    ),
)


def strip_bom(data: bytes) -> bytes:
    """Remove a leading UTF-8 BOM from data, if present."""
    if data.startswith(BOM):
        return data[len(BOM):]
    return data


def iter_file_chunks(path: Path, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
    """Yield a file's bytes in chunks, stripping a leading UTF-8 BOM once.

    The first read takes at least len(BOM) bytes so the BOM is detected even
    for small chunk sizes. Raw files on disk are never modified.
    """
    with path.open("rb") as handle:
        first = strip_bom(handle.read(max(chunk_size, len(BOM))))
        if first:
            yield first
        while chunk := handle.read(chunk_size):
            yield chunk


def copy_statement(load: TableLoad) -> sql.Composed:
    """Build ``COPY <table> (<columns>) FROM STDIN WITH (...)``."""
    columns = sql.SQL(", ").join(sql.Identifier(name) for name in load.columns)
    return sql.SQL(
        "COPY {} ({}) FROM STDIN WITH "
        "(FORMAT csv, HEADER true, NULL '', ENCODING 'UTF8')"
    ).format(sql.Identifier(load.table), columns)


def load_table(conn: Connection, load: TableLoad, raw_dir: Path) -> None:
    """Stream one CSV file into its table with COPY."""
    with conn.cursor() as cur:
        with cur.copy(copy_statement(load)) as copy:
            for chunk in iter_file_chunks(raw_dir / load.filename):
                copy.write(chunk)


def missing_tables(conn: Connection, specs: Sequence[TableLoad]) -> list[str]:
    """Return target tables that do not exist in the public schema."""
    absent: list[str] = []
    with conn.cursor() as cur:
        for spec in specs:
            cur.execute("SELECT to_regclass(%s)", (f"public.{spec.table}",))
            if cur.fetchone()[0] is None:
                absent.append(spec.table)
    return absent


def populated_tables(conn: Connection, specs: Sequence[TableLoad]) -> list[str]:
    """Return target tables that already contain at least one row."""
    populated: list[str] = []
    with conn.cursor() as cur:
        for spec in specs:
            cur.execute(
                sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(
                    sql.Identifier(spec.table)
                )
            )
            if cur.fetchone()[0]:
                populated.append(spec.table)
    return populated


def truncate_all(conn: Connection, specs: Sequence[TableLoad]) -> None:
    """Truncate every target table in one statement, resetting identity."""
    tables = sql.SQL(", ").join(sql.Identifier(spec.table) for spec in specs)
    with conn.cursor() as cur:
        # All inter-dependent tables are listed together, so CASCADE is not
        # needed and unrelated tables can never be affected.
        cur.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY").format(tables))


def print_counts(conn: Connection, specs: Sequence[TableLoad]) -> None:
    """Print the row count of every target table."""
    print("\nLoaded row counts:")
    with conn.cursor() as cur:
        for spec in specs:
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(spec.table))
            )
            print(f"  {spec.table:<28} {cur.fetchone()[0]:>8}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ai_sql_analyst.ingest",
        description="Load the Olist CSVs into PostgreSQL with COPY.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="truncate the nine target tables before reloading",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=REPO_ROOT / "data" / "raw",
        help="directory containing the Olist CSV files "
        "(default: <repo>/data/raw)",
    )
    return parser.parse_args(argv)


def run(raw_dir: Path, replace: bool) -> int:
    """Validate inputs and load all tables in a single transaction."""
    missing_files = [
        spec.filename for spec in LOAD_SPECS if not (raw_dir / spec.filename).is_file()
    ]
    if missing_files:
        print(f"Missing CSV files in {raw_dir}:", file=sys.stderr)
        for name in missing_files:
            print(f"  - {name}", file=sys.stderr)
        return 1

    # ``with connect() as conn`` commits on success, rolls back on error, and
    # closes the connection: the whole load is one transaction.
    with connect() as conn:
        absent = missing_tables(conn, LOAD_SPECS)
        if absent:
            print("Missing tables: " + ", ".join(absent), file=sys.stderr)
            print(
                "Apply backend/sql/schema.sql before ingesting.", file=sys.stderr
            )
            return 1

        existing = populated_tables(conn, LOAD_SPECS)
        if existing and not replace:
            print(
                "Target tables already contain data: " + ", ".join(existing),
                file=sys.stderr,
            )
            print("Re-run with --replace to reset and reload.", file=sys.stderr)
            return 1

        if existing:
            truncate_all(conn, LOAD_SPECS)

        for spec in LOAD_SPECS:
            print(f"Loading {spec.table} from {spec.filename} ...", flush=True)
            load_table(conn, spec, raw_dir)

        print_counts(conn, LOAD_SPECS)

    print("\nIngestion complete.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return run(args.raw_dir, args.replace)


if __name__ == "__main__":
    sys.exit(main())
