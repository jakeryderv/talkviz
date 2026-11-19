from datetime import date

import duckdb
import pytest
import yaml

from talkviz.manifest import Manifest
from talkviz.sources import add_source
from talkviz.sql import connect

MANIFEST = {
    "name": "shop",
    "title": "Shop orders",
    "description": "Orders placed at a small shop.",
    "dialect": "duckdb",
    "data_version_sql": "SELECT max(ordered) FROM orders",
    "caveats": ["Refunds are not included."],
    "tables": [
        {
            "name": "orders",
            "description": "One row per order.",
            "columns": [
                {"name": "id", "type": "INTEGER", "description": "Order number."},
                {"name": "ordered", "type": "DATE", "description": "Order date."},
                {
                    "name": "category",
                    "type": "VARCHAR",
                    "description": "Product category.",
                    "values": ["books", "games", "toys"],
                },
                {"name": "amount", "type": "DOUBLE", "description": "Order total."},
            ],
        }
    ],
    "examples": [
        {"question": "How many orders?", "sql": "SELECT count(*) FROM orders"}
    ],
}

ORDERS = [
    (1, date(2024, 1, 5), "books", 12.5),
    (2, date(2024, 1, 9), "games", 60.0),
    (3, date(2024, 2, 1), "books", 20.0),
    (4, date(2024, 2, 14), "toys", 35.0),
    (5, date(2024, 3, 3), "games", 45.0),
    (6, date(2024, 3, 8), "books", 8.0),
]


@pytest.fixture
def manifest():
    return Manifest.model_validate(MANIFEST)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(path) as con:
        con.execute(
            "CREATE TABLE orders (id INTEGER, ordered DATE, category VARCHAR,"
            " amount DOUBLE)"
        )
        con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", ORDERS)
        con.execute("CREATE TABLE secrets (token VARCHAR)")
    return path


@pytest.fixture
def con(database):
    with connect(database) as connection:
        yield connection


@pytest.fixture
def source(database, tmp_path, monkeypatch):
    monkeypatch.setenv("TALKVIZ_HOME", str(tmp_path / "home"))
    manifest = tmp_path / "shop.yaml"
    manifest.write_text(yaml.safe_dump(MANIFEST))
    return add_source(database, manifest)
