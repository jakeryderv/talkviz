import pytest
from pydantic import TypeAdapter, ValidationError

from talkviz.charts import (
    Bar,
    ChartSpec,
    Heatmap,
    Line,
    Parts,
    Scatter,
    Stat,
    Table,
    check_spec,
    views,
)
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


TOTALS = "SELECT category, sum(amount) AS total FROM orders GROUP BY 1"
POINTS = "SELECT id, amount, id * 2 AS doubled, category FROM orders"


def test_new_kinds_fit_the_results_they_are_for(con):
    totals, by_month, points = run(con, TOTALS), run(con, BY_MONTH), run(con, POINTS)
    fits = [
        (Parts(kind="donut", label="category", value="total"), totals),
        (Parts(kind="treemap", label="category", value="total"), totals),
        (Bar(kind="stacked_bar", x="month", y="total", series="category"), by_month),
        (Line(kind="area", x="month", y="total", series="category"), by_month),
        (Heatmap(kind="heatmap", x="month", y="category", value="total"), by_month),
        (Scatter(kind="scatter", x="id", y="amount", color="category"), points),
        (Scatter(kind="scatter3d", x="id", y="amount", z="doubled"), points),
    ]
    for spec, result in fits:
        assert check_spec(spec, result) == [], spec


@pytest.mark.parametrize(
    ("spec", "sql", "problem"),
    [
        (
            Parts(kind="donut", label="category", value="amount"),
            "SELECT category, amount FROM orders",
            "several rows",
        ),
        (
            Parts(kind="donut", label="name", value="i"),
            "SELECT i, i::VARCHAR AS name FROM range(1, 30) t(i)",
            "too many for a donut",
        ),
        (
            Parts(kind="donut", label="category", value="total"),
            "SELECT category, -sum(amount) AS total FROM orders GROUP BY 1",
            "negative",
        ),
        (Bar(kind="stacked_bar", x="category", y="total"), TOTALS, "needs a series"),
        (
            Heatmap(kind="heatmap", x="category", y="category", value="amount"),
            "SELECT category, amount FROM orders",
            "several rows",
        ),
        (
            Scatter(kind="scatter", x="category", y="amount"),
            POINTS,
            "must be number or time",
        ),
        (Scatter(kind="scatter3d", x="id", y="amount"), POINTS, "needs a z column"),
        (
            Scatter(kind="scatter", x="id", y="amount", z="doubled"),
            POINTS,
            "Only scatter3d",
        ),
        (Scatter(kind="scatter", x="id", y="amount", color="id"), POINTS, None),
        (
            Parts(kind="treemap", label="id", value="amount"),
            POINTS,
            "must be text or boolean",
        ),
    ],
)
def test_new_kinds_that_do_not_fit_are_explained(spec, sql, problem, con):
    problems = check_spec(spec, run(con, sql))
    if problem is None:
        assert problems == []  # a numeric colour has no category limit
    else:
        assert any(problem in p for p in problems), problems


def kinds(spec, result):
    return [view.kind for view in views(spec, result)]


def test_views_list_every_way_to_draw_the_same_result(con):
    totals, by_month, points = run(con, TOTALS), run(con, BY_MONTH), run(con, POINTS)
    bar = Bar(kind="bar", x="category", y="total")
    assert kinds(bar, totals) == ["bar", "donut", "treemap", "table"]
    lines = Line(kind="line", x="month", y="total", series="category")
    assert kinds(lines, by_month) == [
        "line",
        "area",
        "bar",
        "stacked_bar",
        "heatmap",
        "table",
    ]
    donut = Parts(kind="donut", label="category", value="total")
    assert kinds(donut, totals) == ["donut", "bar", "treemap", "table"]
    cube = Scatter(kind="scatter3d", x="id", y="amount", z="doubled")
    assert kinds(cube, points) == ["scatter3d", "scatter", "table"]
    count = run(con, "SELECT count(*) AS orders FROM orders")
    assert kinds(Stat(kind="stat", value="orders", label="orders"), count) == [
        "stat",
        "table",
    ]
    assert kinds(Table(kind="table"), totals) == ["table"]


def test_views_keep_the_columns_in_their_roles(con):
    lines = Line(kind="line", x="month", y="total", series="category")
    heatmap = next(v for v in views(lines, run(con, BY_MONTH)) if v.kind == "heatmap")
    assert heatmap == Heatmap(kind="heatmap", x="month", y="category", value="total")
    cube = Scatter(kind="scatter3d", x="id", y="amount", z="doubled")
    flat = views(cube, run(con, POINTS))[1]
    assert flat == Scatter(kind="scatter", x="id", y="amount", color="doubled")
