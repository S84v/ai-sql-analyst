"""Tests for framework-neutral schema introspection.

The ``build_schema`` tests use hand-built catalogs and need no database. The
live-database tests are opt-in via RUN_DB_TESTS=1, so the default ``pytest``
run stays database-free (matching CI).
"""

from __future__ import annotations

import json
import os

import pytest

from ai_sql_analyst.db import connect
from ai_sql_analyst.schema import Catalog, build_schema, get_schema

# ---------------------------------------------------------------------------
# Pure transformation tests (no database)
# ---------------------------------------------------------------------------

SAMPLE = Catalog(
    tables=(
        (20, "sellers", None),
        (10, "orders", "Order grain comment."),
    ),
    columns=(
        (10, 3, "order_status", "text", False, "status comment"),
        (10, 1, "order_id", "text", False, "id comment"),
        (10, 4, "order_approved_at", "timestamp without time zone", True, None),
        (10, 2, "customer_id", "text", False, None),
        (20, 1, "seller_id", "text", False, None),
    ),
    primary_keys=((10, (1,)), (20, (1,))),
    foreign_keys=((10, (2,), 20, (1,)),),
    checks=((10, "CHECK (b)"), (10, "CHECK (a)")),
)


def test_tables_are_sorted_by_name():
    schema = build_schema(SAMPLE)
    assert [table.name for table in schema.tables] == ["orders", "sellers"]


def test_columns_follow_physical_order():
    orders = build_schema(SAMPLE).tables[0]
    assert [column.name for column in orders.columns] == [
        "order_id",
        "customer_id",
        "order_status",
        "order_approved_at",
    ]


def test_types_nullability_and_comments_preserved():
    orders = build_schema(SAMPLE).tables[0]
    by_name = {column.name: column for column in orders.columns}
    assert by_name["order_id"].data_type == "text"
    assert by_name["order_id"].comment == "id comment"
    assert by_name["order_approved_at"].data_type == "timestamp without time zone"
    assert by_name["order_approved_at"].nullable is True
    assert by_name["order_approved_at"].comment is None
    assert by_name["order_status"].nullable is False


def test_primary_key_resolved_in_order():
    orders = build_schema(SAMPLE).tables[0]
    assert orders.primary_key == ("order_id",)


def test_foreign_key_resolved_to_column_names():
    orders = build_schema(SAMPLE).tables[0]
    assert len(orders.foreign_keys) == 1
    fk = orders.foreign_keys[0]
    assert fk.columns == ("customer_id",)
    assert fk.references_table == "sellers"
    assert fk.references_columns == ("seller_id",)


def test_checks_are_sorted_without_names():
    orders = build_schema(SAMPLE).tables[0]
    assert orders.checks == ("CHECK (a)", "CHECK (b)")


def test_table_without_primary_key():
    catalog = Catalog(
        tables=((7, "geolocation", None),),
        columns=((7, 1, "geolocation_id", "bigint", False, None),),
        primary_keys=(),
        foreign_keys=(),
        checks=(),
    )
    table = build_schema(catalog).tables[0]
    assert table.primary_key == ()
    assert table.foreign_keys == ()
    assert table.checks == ()


def test_composite_primary_key_and_foreign_key():
    catalog = Catalog(
        tables=((1, "order_items", None), (2, "orders", None)),
        columns=(
            (1, 1, "order_id", "text", False, None),
            (1, 2, "order_item_id", "integer", False, None),
            (2, 1, "order_id", "text", False, None),
            (2, 2, "payment_sequential", "integer", False, None),
        ),
        primary_keys=((1, (1, 2)), (2, (1, 2))),
        foreign_keys=((1, (1, 2), 2, (1, 2)),),
        checks=(),
    )
    order_items = build_schema(catalog).tables[0]
    assert order_items.primary_key == ("order_id", "order_item_id")
    assert order_items.foreign_keys[0].columns == ("order_id", "order_item_id")
    assert order_items.foreign_keys[0].references_columns == (
        "order_id",
        "payment_sequential",
    )


def test_build_schema_is_deterministic():
    assert build_schema(SAMPLE) == build_schema(SAMPLE)


def test_to_dict_shape_and_json_roundtrip():
    payload = build_schema(SAMPLE).to_dict()
    assert json.loads(json.dumps(payload)) == payload

    table = payload["tables"][0]
    assert set(table) == {
        "name",
        "comment",
        "primary_key",
        "columns",
        "foreign_keys",
        "checks",
    }
    assert table["name"] == "orders"
    assert table["comment"] == "Order grain comment."
    assert table["primary_key"] == ["order_id"]
    assert set(table["columns"][0]) == {"name", "data_type", "nullable", "comment"}
    assert set(table["foreign_keys"][0]) == {
        "columns",
        "references_table",
        "references_columns",
    }
    assert table["checks"] == ["CHECK (a)", "CHECK (b)"]


# ---------------------------------------------------------------------------
# Live-database tests (opt-in; CI has no database)
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "customers",
    "sellers",
    "products",
    "product_category_translation",
    "geolocation",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
}


@pytest.fixture(scope="module")
def live_schema():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    # RUN_DB_TESTS=1 is explicit opt-in: a connection/config failure must fail
    # the test rather than skip it.
    with connect() as connection:
        schema = get_schema(connection)
    return schema


@pytest.fixture(scope="module")
def live_connection():
    if os.environ.get("RUN_DB_TESTS") != "1":
        pytest.skip("set RUN_DB_TESTS=1 to run database integration tests")
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def _table(schema, name):
    return next(table for table in schema.tables if table.name == name)


def test_live_schema_covers_all_tables(live_schema):
    assert {table.name for table in live_schema.tables} == EXPECTED_TABLES


def test_live_orders_key_and_foreign_key(live_schema):
    orders = _table(live_schema, "orders")
    assert orders.primary_key == ("order_id",)
    assert any(
        fk.columns == ("customer_id",) and fk.references_table == "customers"
        for fk in orders.foreign_keys
    )


def test_live_order_reviews_composite_key(live_schema):
    reviews = _table(live_schema, "order_reviews")
    assert reviews.primary_key == ("review_id", "order_id")


def test_live_comments_preserve_semantics(live_schema):
    customers = _table(live_schema, "customers")
    columns = {column.name: column for column in customers.columns}
    assert "Per-order" in (columns["customer_id"].comment or "")
    assert "Persistent" in (columns["customer_unique_id"].comment or "")

    products = _table(live_schema, "products")
    category = {column.name: column for column in products.columns}["product_category_name"]
    assert "translation" in (category.comment or "")

    assert "ZIP prefix" in (_table(live_schema, "geolocation").comment or "")
    assert "not unique" in (_table(live_schema, "order_reviews").comment or "")
    assert "fan-out" in (_table(live_schema, "order_payments").comment or "")


def test_live_checks_present_for_orders(live_schema):
    orders = _table(live_schema, "orders")
    assert any("delivered" in check for check in orders.checks)


def test_live_schema_is_serializable(live_schema):
    assert json.loads(json.dumps(live_schema.to_dict()))


def test_get_schema_leaves_caller_connection_open(live_connection):
    get_schema(live_connection)
    assert not live_connection.closed
    assert live_connection.execute("SELECT 1").fetchone() == (1,)
