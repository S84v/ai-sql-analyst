"""Focused tests for pure Olist ingestion behavior (no database required)."""

from __future__ import annotations

import codecs
from pathlib import Path

from ai_sql_analyst import ingest
from ai_sql_analyst.db import REPO_ROOT

EXPECTED_ORDER = [
    "customers",
    "sellers",
    "products",
    "product_category_translation",
    "geolocation",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
]

# child table -> tables it references; those must load first.
DEPENDENCIES = {
    "orders": {"customers"},
    "order_items": {"orders", "products", "sellers"},
    "order_payments": {"orders"},
    "order_reviews": {"orders"},
}


def test_strip_bom_removes_utf8_bom():
    assert ingest.strip_bom(codecs.BOM_UTF8 + b"abc") == b"abc"


def test_strip_bom_leaves_other_bytes_unchanged():
    assert ingest.strip_bom(b"abc") == b"abc"
    assert ingest.strip_bom(b"") == b""


def test_iter_file_chunks_strips_bom(tmp_path: Path):
    path = tmp_path / "with_bom.csv"
    path.write_bytes(codecs.BOM_UTF8 + b"a,b\n1,2\n")
    assert b"".join(ingest.iter_file_chunks(path)) == b"a,b\n1,2\n"


def test_iter_file_chunks_roundtrips_without_bom(tmp_path: Path):
    content = b"a,b\n1,2\n3,4\n"
    path = tmp_path / "plain.csv"
    path.write_bytes(content)
    assert b"".join(ingest.iter_file_chunks(path)) == content


def test_iter_file_chunks_small_chunk_size_still_strips_bom(tmp_path: Path):
    content = b"a,b\n1,2\n3,4\n"
    path = tmp_path / "small.csv"
    path.write_bytes(codecs.BOM_UTF8 + content)
    chunks = list(ingest.iter_file_chunks(path, chunk_size=1))
    assert all(len(chunk) <= 1 for chunk in chunks)
    assert b"".join(chunks) == content


def test_load_specs_table_order_is_dependency_safe():
    tables = [spec.table for spec in ingest.LOAD_SPECS]
    assert tables == EXPECTED_ORDER
    index = {name: position for position, name in enumerate(tables)}
    for child, parents in DEPENDENCIES.items():
        for parent in parents:
            assert index[parent] < index[child], (parent, child)


def test_load_specs_are_unique_and_well_formed():
    tables = [spec.table for spec in ingest.LOAD_SPECS]
    filenames = [spec.filename for spec in ingest.LOAD_SPECS]
    assert len(tables) == len(set(tables)) == 9
    assert len(filenames) == len(set(filenames)) == 9
    for spec in ingest.LOAD_SPECS:
        assert spec.columns, spec.table
        assert len(spec.columns) == len(set(spec.columns)), spec.table


def test_geolocation_excludes_generated_id():
    geolocation = next(s for s in ingest.LOAD_SPECS if s.table == "geolocation")
    assert "geolocation_id" not in geolocation.columns
    assert geolocation.columns[0] == "geolocation_zip_code_prefix"


def test_parse_args_defaults():
    args = ingest.parse_args([])
    assert args.replace is False
    assert args.raw_dir == REPO_ROOT / "data" / "raw"


def test_parse_args_overrides():
    args = ingest.parse_args(["--replace", "--raw-dir", "/tmp/olist"])
    assert args.replace is True
    assert args.raw_dir == Path("/tmp/olist")
