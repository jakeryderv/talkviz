import pytest
from pydantic import TypeAdapter, ValidationError

from talkviz.charts import Bar, ChartSpec, Line, Stat, Table, check_spec
from talkviz.sql import run

BY_MONTH = (
    "SELECT date_trunc('month', ordered) AS month, category, sum(amount) AS total"
    " FROM orders GROUP BY ALL ORDER BY month"
)


def test_specs_parse_by_kind_and_reject_unknown_fields():
    adapter = TypeAdapter(ChartSpec)
    assert adapter.validate_python({"kind": "bar", "x": "a", "y": "b"}) == Bar(
        kind="bar", x="a", y="b"
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "pie", "x": "a", "y": "b"})
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "bar", "x": "a", "y": "b", "color": "red"})


def test_valid_specs_have_no_problems(con):
    totals = run(con, "SELECT category, sum(amount) AS total FROM orders GROUP BY 1")
    assert check_spec(Bar(kind="bar", x="category", y="total"), totals) == []
    assert check_spec(Table(kind="table"), totals) == []
    by_month = run(con, BY_MONTH)
    line = Line(kind="line", x="month", y="total", series="category")
    assert check_spec(line, by_month) == []
    count = run(con, "SELECT count(*) AS orders FROM orders")
    assert check_spec(Stat(kind="stat", value="orders", label="orders"), count) == []


@pytest.mark.parametrize(
    ("spec", "sql", "problem"),
    [
        (Bar(kind="bar", x="kind", y="total"), "SELECT 1 AS total", "'kind' is not in"),
        (
            Bar(kind="bar", x="category", y="category"),
            "SELECT category FROM orders GROUP BY 1",
            "'category' is text; it must be number",
        ),
        (
            Line(kind="line", x="category", y="total"),
            "SELECT category, sum(amount) AS total FROM orders GROUP BY 1",
            "must be number or time",
        ),
        (
            Bar(kind="bar", x="category", y="amount"),
            "SELECT category, amount FROM orders",
            "several rows for the same 'category'",
        ),
        (
            Line(kind="line", x="month", y="total"),
            BY_MONTH,
            "several rows for the same 'month'",
        ),
        (
            Stat(kind="stat", value="amount", label="amount"),
            "SELECT amount FROM orders",
            "one row",
        ),
        (
            Line(kind="line", x="id", y="amount"),
            "SELECT id, amount FROM orders WHERE id = 1",
            "at least two",
        ),
        (Table(kind="table"), "SELECT * FROM orders WHERE id < 0", "no rows"),
    ],
)
def test_specs_that_do_not_fit_the_result_are_explained(spec, sql, problem, con):
    problems = check_spec(spec, run(con, sql))
    assert any(problem in p for p in problems), problems


def test_too_many_bars_or_series(con):
    many = run(con, "SELECT i AS n, i * 2 AS doubled FROM range(100) t(i)")
    assert any(
        "too many" in p for p in check_spec(Bar(kind="bar", x="n", y="doubled"), many)
    )
    spread = run(con, "SELECT 1 AS x, i AS s, i AS y FROM range(20) t(i)")
    spec = Bar(kind="bar", x="x", y="y", series="s")
    assert any("at most 8 series" in p for p in check_spec(spec, spread))
