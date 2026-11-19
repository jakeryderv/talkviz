"""Moving around a chart: zooming, hiding series, and resetting."""

import pytest
from test_llm import Scripted
from test_route import Fixed, answer, jev

from talkviz import llm
from talkviz.charts import Line, Table, axis, fit_lens
from talkviz.route import Shortcut, _decide
from talkviz.sql import run
from talkviz.turn import take_turn
from talkviz.workspace import Chart, Lens, SetLens, Workspace

# Sales for each month and category: three months, three categories.
MONTHLY = (
    "SELECT month(ordered) AS month, category, sum(amount) AS total"
    " FROM orders GROUP BY 1, 2 ORDER BY 1, 2"
)
SPEC = {"kind": "line", "x": "month", "y": "total", "series": "category"}
CHART = Chart.model_validate(
    {"id": "c1", "title": "Monthly sales", "sql": MONTHLY, "spec": SPEC}
)


def lens_action(**fields) -> SetLens:
    return SetLens(op="set_lens", chart_id="c1", **fields)


def test_axis_lists_x_values_and_series_as_text(con):
    result = run(con, MONTHLY)
    assert axis(Line.model_validate(SPEC), result) == (
        ["1", "2", "3"],
        ["books", "games", "toys"],
    )
    # Kinds that have no x axis to zoom offer nothing.
    assert axis(Table(kind="table"), result) == ([], [])


def test_fit_lens_matches_names_and_orders_the_range(con):
    spec, result = Line.model_validate(SPEC), run(con, MONTHLY)
    assert fit_lens(spec, result, "3", "2", ["Games"]) == ("2", "3", ["games"])
    assert fit_lens(spec, result, None, None, []) == (None, None, [])


@pytest.mark.parametrize(
    ("start", "end", "hidden", "problem"),
    [
        ("2", "9", [], "'9' is not one of the chart's x values"),
        ("2", None, [], "both ends"),
        (None, None, ["shoes"], "'shoes' is not one of the chart's series"),
        (None, None, ["books", "games", "toys"], "At least one series"),
    ],
)
def test_fit_lens_explains_what_does_not_apply(con, start, end, hidden, problem):
    with pytest.raises(ValueError, match=problem):
        fit_lens(Line.model_validate(SPEC), run(con, MONTHLY), start, end, hidden)


def test_a_lens_builds_up_and_resets_without_a_revision():
    workspace = Workspace(charts=[CHART])
    for action, expected in [
        (lens_action(start="2", end="3"), Lens(start="2", end="3")),
        (lens_action(hide=["games"]), Lens(start="2", end="3", hidden=["games"])),
        (
            lens_action(hide=["toys"], show=["games"]),
            Lens(start="2", end="3", hidden=["toys"]),
        ),
        (lens_action(zoom_out=True), Lens(hidden=["toys"])),
        (lens_action(start="1", end="2"), Lens(start="1", end="2", hidden=["toys"])),
        (lens_action(reset=True, hide=["books"]), Lens(hidden=["books"])),
        (lens_action(reset=True), None),
    ]:
        chart = workspace.resolve(action)
        workspace.apply(chart)
        assert (chart.lens, chart.revision) == (expected, 1)


def test_changing_a_chart_puts_all_of_it_back_in_view():
    chart = CHART.model_copy(update={"lens": Lens(hidden=["games"])})
    update = {"op": "update_chart", "chart_id": "c1", "title": "Sales"}
    plan = llm.Plan.model_validate({"actions": [update]})
    revised = Workspace(charts=[chart]).resolve(plan.actions[0])
    assert (revised.lens, revised.revision) == (None, 2)


def test_the_planner_can_zoom_and_is_told_what_does_not_apply(manifest, con):
    workspace = Workspace(charts=[CHART])
    model = Scripted(
        {"actions": [{"op": "set_lens", "chart_id": "c1", "start": "2", "end": "7"}]},
        {
            "actions": [
                {
                    "op": "set_lens",
                    "chart_id": "c1",
                    "start": "3",
                    "end": "2",
                    "hide": ["GAMES"],
                }
            ]
        },
    )
    turn = take_turn(
        model, "zoom to February on, no games", manifest, con, workspace, []
    )
    assert "'7' is not one of the chart's x values: 1, 2, 3." in model.sent(1)
    assert workspace.charts[0].lens == Lens(start="2", end="3", hidden=["games"])
    # Nothing about the data changed, so the reply needs no model.
    assert turn.reply == "Monthly sales now shows 2 to 3 and hides games."
    assert "in view:" in llm._plan_prompt("x", workspace, [], None)


def test_resetting_says_so(manifest, con):
    chart = CHART.model_copy(update={"lens": Lens(hidden=["games"])})
    workspace = Workspace(charts=[chart])
    model = Scripted({"actions": [{"op": "set_lens", "chart_id": "c1", "reset": True}]})
    turn = take_turn(model, "zoom out", manifest, con, workspace, [])
    assert turn.reply == "Monthly sales is back to its full view."
    assert workspace.charts[0].lens is None


def routed(route, c1, c2, sure=0.95):
    return {
        "route": answer(route, sure),
        "kind": answer("none"),
        "c1": {"type": "noul", "noul": c1},
        "c2": {"type": "noul", "noul": c2},
    }


def test_navigation_means_the_chart_on_screen_unless_one_is_named():
    other = CHART.model_copy(update={"id": "c2", "title": "Orders"})
    workspace = Workspace(charts=[CHART, other])
    assert _decide(routed("navigate", 0.3, 0.4), workspace, "c2") == Shortcut(
        "navigate", ("c2",)
    )
    assert _decide(routed("navigate", 0.9, 0.1), workspace, "c2") == Shortcut(
        "navigate", ("c1",)
    )
    assert _decide(routed("navigate", 0.9, 0.9), workspace, "c2") is None
    assert _decide(routed("navigate", 0.9, 0.1, sure=0.6), workspace, "c2") is None


def moves(action, start="none", end="none", books=0.05, games=0.05, sure=0.95):
    return {
        "action": answer(action, sure),
        "from": answer(start),
        "to": answer(end),
        "s0": {"type": "noul", "noul": books},
        "s1": {"type": "noul", "noul": games},
    }


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (moves("zoom", "2", "3"), lens_action(start="2", end="3")),
        (moves("hide", games=0.96), lens_action(hide=["games"])),
        (moves("show", books=0.9, games=0.9), lens_action(show=["books", "games"])),
        (moves("reset"), lens_action(reset=True)),
        (moves("zoom_out"), lens_action(zoom_out=True)),
        # Unsure, or nothing to act on: the planner's.
        (moves("zoom", "2", "3", sure=0.5), None),
        (moves("zoom", "2", "none"), None),
        (moves("hide"), None),
        (moves("hide", games=0.5), None),
        (moves("none"), None),
    ],
)
def test_the_router_picks_a_range_or_series_from_the_charts_own(answers, expected):
    router, sent = jev(answers)
    found = router.navigate("zoom in", CHART, ["1", "2", "3"], ["books", "games"])
    assert found == expected
    assert set(sent[0]["questions"]["from"]["criteria"]) == {"1", "2", "3", "none"}
    assert sent[0]["state"]["chart"]["series"] == ["books", "games"]


def test_a_navigation_shortcut_calls_no_language_model(manifest, con):
    workspace = Workspace(charts=[CHART])
    router = Fixed(Shortcut("navigate", ("c1",)), lens_action(start="3", end="2"))
    turn = take_turn(Scripted(), "zoom in", manifest, con, workspace, [], router=router)
    assert router.offered == (["1", "2", "3"], ["books", "games", "toys"])
    assert (turn.shortcut, turn.reply) == (True, "Monthly sales now shows 2 to 3.")
    assert workspace.charts[0].lens == Lens(start="2", end="3")


def test_navigation_the_router_gets_wrong_goes_to_the_planner(manifest, con):
    workspace = Workspace(charts=[CHART])
    router = Fixed(Shortcut("navigate", ("c1",)), lens_action(hide=["shoes"]))
    model = Scripted({"actions": [], "note": "No shoes."}, "There is no shoes series.")
    turn = take_turn(model, "hide shoes", manifest, con, workspace, [], router=router)
    assert (turn.shortcut, turn.reply) == (False, "There is no shoes series.")


def test_showing_the_zoomed_chart_on_screen_again_is_navigation(manifest, con):
    chart = CHART.model_copy(update={"lens": Lens(hidden=["games"])})
    workspace = Workspace(charts=[chart])
    router = Fixed(Shortcut("show_chart", ("c1",)), lens_action(reset=True))
    turn = take_turn(
        Scripted(),
        "show everything again",
        manifest,
        con,
        workspace,
        [],
        router=router,
        focus="c1",
    )
    assert turn.reply == "Monthly sales is back to its full view."
