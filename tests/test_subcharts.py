"""Sub-charts: a filter makes a lettered child chart; the original stays."""

import pytest
from test_llm import TOTALS, Scripted, create

from talkviz.charts import Condition, Narrow, narrowed
from talkviz.llm import Plan
from talkviz.sql import run
from talkviz.turn import take_turn
from talkviz.workspace import Chart, NarrowChart, NoRoom, Workspace, run_chart

MONTHLY = (
    "SELECT month(ordered) AS month, category, sum(amount) AS total"
    " FROM orders GROUP BY 1, 2 ORDER BY 1, 2"
)
BAR = {"kind": "bar", "x": "category", "y": "total"}
PARENT = Chart.model_validate(
    {"id": "c1", "title": "Sales by category", "sql": TOTALS, "spec": BAR}
)


def keep(**fields) -> Narrow:
    return Narrow.model_validate(fields)


def narrow(title="Books and toys", **fields) -> dict:
    return {"op": "narrow_chart", "chart_id": "c1", "title": title, **fields}


def test_narrowing_keeps_rows_and_never_changes_them(con):
    result = run(con, MONTHLY)
    by_value = keep(where=[{"column": "category", "values": ["Books", "toys"]}])
    kept = narrowed(result, by_value)
    assert {row[1] for row in kept.rows} == {"books", "toys"}
    assert set(kept.rows) <= set(result.rows)

    ranged = narrowed(result, keep(where=[{"column": "month", "min": "2"}]))
    assert {row[0] for row in ranged.rows} == {2, 3}

    top = narrowed(result, keep(top=2, by="total"))
    assert sorted(row[2] for row in top.rows) == [45.0, 60.0]
    assert top.rows == [row for row in result.rows if row in top.rows]  # same order


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"where": [{"column": "make", "values": ["x"]}]}, "'make' is not a column"),
        ({"where": [{"column": "category", "values": ["shoes"]}]}, "'shoes' is not"),
        ({"where": [{"column": "category", "min": "b"}]}, "needs a number or date"),
        ({"top": 2}, "`top` needs `by`"),
        ({}, "at least one condition"),
    ],
)
def test_narrowing_explains_what_does_not_apply(con, fields, problem):
    with pytest.raises(ValueError, match=problem):
        narrowed(run(con, MONTHLY), keep(**fields))


def test_sub_charts_are_lettered_under_their_parent():
    workspace = Workspace(charts=[PARENT])
    workspace.apply(workspace.resolve(_action(create(title="Monthly"))))
    for expected in ("c1a", "c1b"):
        child = workspace.resolve(_action(narrow(title=expected, top=1, by="total")))
        workspace.apply(child)
        assert (child.id, child.parent, child.label) == (expected, "c1", expected[1:])
    # Each sub-chart sits after its parent, and top-level numbering skips them.
    assert [chart.id for chart in workspace.charts] == ["c1", "c1a", "c1b", "c2"]
    assert workspace.resolve(_action(create(title="Third"))).id == "c3"


def _action(fields: dict):
    return Plan.model_validate({"actions": [fields]}).actions[0]


def test_one_level_and_twenty_six_at_most():
    child = PARENT.model_copy(update={"id": "c1a", "parent": "c1"})
    workspace = Workspace(charts=[PARENT, child])
    with pytest.raises(NoRoom, match="cannot have sub-charts"):
        workspace.sub_id("c1a")
    letters = "abcdefghijklmnopqrstuvwxyz"
    full = [PARENT.model_copy(update={"id": f"c1{x}", "parent": "c1"}) for x in letters]
    with pytest.raises(NoRoom, match="already has 26"):
        Workspace(charts=[PARENT, *full]).sub_id("c1")


def test_a_filter_makes_a_sub_chart_and_leaves_the_original(manifest, con):
    workspace = Workspace(charts=[PARENT])
    where = [{"column": "category", "values": ["books", "toys"]}]
    model = Scripted({"actions": [narrow(where=where)]}, "Books 40.5 and toys 35.")
    turn = take_turn(model, "just books and toys", manifest, con, workspace, [])
    [made] = turn.runs
    assert (made.chart.id, made.op, len(made.result.rows)) == ("c1a", "narrow_chart", 2)
    assert made.chart.spec == PARENT.spec
    assert workspace.charts[0] == PARENT


def test_the_planner_is_told_when_rows_are_not_there_or_there_is_no_room(manifest, con):
    workspace = Workspace(charts=[PARENT])
    since = (
        "SELECT category, sum(amount) AS total FROM orders"
        " WHERE ordered >= DATE '2024-02-01' GROUP BY category"
    )
    model = Scripted(
        {"actions": [narrow(where=[{"column": "month", "min": "2"}])]},
        {"actions": [{**create(title="Since February", sql=since), "parent": "c1"}]},
        "Since February, games lead.",
    )
    turn = take_turn(model, "only since February", manifest, con, workspace, [])
    assert "use create_chart with `parent`" in model.sent(1)
    made = turn.runs[0].chart
    assert (made.id, made.narrow, made.based_on) == ("c1a", None, 1)

    model = Scripted(
        {"actions": [{**narrow(top=1, by="total"), "chart_id": "c1a"}]},
        {"actions": [], "note": "Chart 1a is a sub-chart; want a new chart instead?"},
        "Chart 1a is a sub-chart, so I can make that a new chart instead.",
    )
    take_turn(model, "top one of that", manifest, con, workspace, [])
    assert "cannot have sub-charts of their own" in model.sent(1)
    assert len(workspace.charts) == 2


def test_a_narrowing_sub_chart_follows_its_parents_query(manifest, con):
    child = Workspace(charts=[PARENT]).resolve(
        NarrowChart(op="narrow_chart", chart_id="c1", title="Top", top=1, by="total")
    )
    assert child.narrow == Narrow(where=[], top=1, by="total")
    assert run_chart(con, manifest, Workspace(charts=[PARENT, child]), child).rows == [
        ("games", 105.0)
    ]
    counts = "SELECT category, count(*) AS total FROM orders GROUP BY category"
    changed = PARENT.model_copy(update={"sql": counts, "revision": 2})
    rows = run_chart(con, manifest, Workspace(charts=[changed, child]), child).rows
    assert rows == [("books", 3)]
    assert Condition(column="x").values is None


def test_a_label_no_chart_has_is_left_to_the_planner(manifest, con):
    from test_route import Fixed

    from talkviz.route import Shortcut

    workspace = Workspace(charts=[PARENT])
    router = Fixed(Shortcut("show_chart", ("c1",)))
    model = Scripted({"actions": [], "note": "There is no chart 1b."}, "No 1b yet.")
    turn = take_turn(
        model,
        "show me 1 and 1b side by side",
        manifest,
        con,
        workspace,
        [],
        router=router,
    )
    assert (turn.shortcut, turn.reply) == (False, "No 1b yet.")


def test_filtering_a_sub_chart_makes_a_sibling_with_both_filters(con, manifest):
    line = {"kind": "line", "x": "month", "y": "total", "series": "category"}
    parent = Chart.model_validate(
        {"id": "c1", "title": "Monthly sales", "sql": MONTHLY, "spec": line}
    )
    workspace = Workspace(charts=[parent])
    some = {"column": "category", "values": ["books", "toys"]}
    workspace.apply(workspace.resolve(_action(narrow(where=[some]))))
    later = {"op": "narrow_chart", "chart_id": "c1a", "title": "From February"}
    since = {"where": [{"column": "month", "min": "2"}]}
    sibling = workspace.resolve(_action(later | since))
    assert (sibling.id, sibling.parent, sibling.sql) == ("c1b", "c1", MONTHLY)
    both = sibling.narrow or Narrow()
    assert [condition.column for condition in both.where] == ["category", "month"]
    rows = run_chart(con, manifest, workspace, sibling).rows
    assert {row[:2] for row in rows} == {(2, "books"), (2, "toys"), (3, "books")}
    # A new top replaces the sub-chart's own; otherwise it is kept.
    top = workspace.resolve(_action(later | {"top": 1, "by": "total"})).narrow
    assert top is not None
    assert (top.top, top.by, len(top.where)) == (1, "total", 1)


def test_a_sub_chart_with_its_own_query_still_cannot_be_filtered_into_another():
    child = PARENT.model_copy(update={"id": "c1a", "parent": "c1"})
    workspace = Workspace(charts=[PARENT, child])
    with pytest.raises(NoRoom, match="cannot have sub-charts"):
        workspace.resolve(
            _action(narrow() | {"chart_id": "c1a", "top": 1, "by": "total"})
        )
