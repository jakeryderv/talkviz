import duckdb
import pytest

from talkviz.sql import QueryRejected, check_and_run, connect, describe, lint, run


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT category, sum(amount) FROM orders GROUP BY category",
        "WITH big AS (SELECT * FROM orders WHERE amount > 20) SELECT count(*) FROM big",
        "SELECT 1 UNION ALL SELECT count(*) FROM orders",
        "SELECT (SELECT max(amount) FROM orders) AS top",
    ],
)
def test_lint_accepts_selects_over_manifest_tables(sql, manifest):
    lint(sql, manifest)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM orders", "Only SELECT"),
        ("CREATE TABLE x AS SELECT 1", "Only SELECT"),
        ("SELECT 1; DROP TABLE orders", "exactly one"),
        ("SELECT * FROM secrets", "Unknown table 'secrets'"),
        ("SELECT * FROM orders JOIN secrets ON true", "Unknown table 'secrets'"),
        ("SELECT (SELECT token FROM secrets)", "Unknown table 'secrets'"),
        ("SELECT * FROM information_schema.tables", "bare table names"),
        ("SELECT * FROM read_csv('/etc/passwd')", "Table functions"),
        ("SELECT * FROM duckdb_tables()", "Table functions"),
        ("SELECT * FROM '/tmp/data.parquet'", "Unknown table"),
        ("PRAGMA database_list", "Only SELECT"),
        ("COPY orders TO '/tmp/orders.csv'", "Only SELECT"),
        ("DESCRIBE orders", "Only SELECT"),
        ("SELECT FROM WHERE", "does not parse"),
        ("SELECT current_setting('home_directory')", "current_setting"),
        ("SELECT CURRENT_SETTING('temp_directory') AS t", "CURRENT_SETTING"),
        ("SELECT count(*) FROM orders WHERE getenv('HOME') > ''", "getenv"),
        ("SELECT current_query()", "current_query"),
        ("SELECT (SELECT current_setting('home_directory'))", "current_setting"),
    ],
)
def test_lint_rejects_everything_else(sql, reason, manifest):
    with pytest.raises(QueryRejected, match=reason):
        lint(sql, manifest)


def test_describe_gives_result_columns_without_running(con):
    columns = describe(
        con,
        "SELECT category, sum(amount) AS total, min(ordered) AS first FROM orders"
        " GROUP BY category",
    )
    assert [(c.name, c.kind) for c in columns] == [
        ("category", "text"),
        ("total", "number"),
        ("first", "time"),
    ]


def test_describe_reports_unknown_columns(con):
    with pytest.raises(QueryRejected, match="price"):
        describe(con, "SELECT price FROM orders")


def test_run_caps_rows_and_says_so(con):
    result = run(con, "SELECT id FROM orders ORDER BY id", max_rows=4)
    assert [row[0] for row in result.rows] == [1, 2, 3, 4]
    assert result.truncated
    assert not run(con, "SELECT id FROM orders", max_rows=6).truncated


def test_run_stops_queries_that_take_too_long(con):
    slow = "SELECT count(*) FROM range(100000000000) t(i) WHERE i % 7 = 3"
    with pytest.raises(QueryRejected, match="longer than"):
        run(con, slow, timeout=0.2)
    # The connection still works afterwards.
    assert run(con, "SELECT count(*) FROM orders").rows == [(6,)]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders VALUES (7, DATE '2024-04-01', 'toys', 1.0)",
        "CREATE TABLE x AS SELECT 1",
        "COPY orders TO '/tmp/talkviz_test_orders.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "ATTACH '/tmp/talkviz_test_other.db'",
        "SET enable_external_access = true",
    ],
)
def test_the_connection_itself_refuses_writes_and_file_access(sql, con):
    # Even if a statement got past lint(), the engine would stop it.
    with pytest.raises(QueryRejected):
        run(con, sql)


def test_check_and_run(con, manifest):
    result = check_and_run(con, "SELECT count(*) AS orders FROM orders", manifest)
    assert result.rows == [(6,)]
    with pytest.raises(QueryRejected):
        check_and_run(con, "SELECT * FROM secrets", manifest)


def test_two_connections_can_be_open_at_once_and_both_are_restricted(database):
    with connect(database) as first, connect(database) as second:
        for con in (first, second):
            assert con.execute("SELECT count(*) FROM orders").fetchone() == (6,)
            with pytest.raises(duckdb.Error):
                con.execute("SET enable_external_access = true")
            with pytest.raises(duckdb.Error):
                con.execute("DELETE FROM orders")
