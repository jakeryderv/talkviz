"""Asking about a chart: answered in words from its rows; nothing changes."""

from test_lens import routed
from test_llm import TOTALS, Scripted
from test_route import Fixed

from talkviz.route import Shortcut, _decide
from talkviz.turn import take_turn
from talkviz.workspace import Chart, Workspace

BAR = {"kind": "bar", "x": "category", "y": "total"}
CHART = Chart.model_validate(
    {"id": "c1", "title": "Sales by category", "sql": TOTALS, "spec": BAR}
)


def test_a_question_about_a_chart_means_the_one_on_screen_unless_named():
    other = CHART.model_copy(update={"id": "c2", "title": "Orders"})
    workspace = Workspace(charts=[CHART, other])
    found = _decide(routed("ask", 0.3, 0.4), workspace, "c2")
    assert found == Shortcut("discuss", ("c2",))
    found = _decide(routed("ask", 0.9, 0.9), workspace, "c2")
    assert found == Shortcut("discuss", ("c1", "c2"))
    assert _decide(routed("ask", 0.9, 0.1, sure=0.6), workspace, "c2") is None


def test_the_router_sends_a_question_straight_to_the_reply(manifest, con):
    workspace = Workspace(charts=[CHART])
    router = Fixed(Shortcut("discuss", ("c1",)))
    # One model call: the reply. No plan is made.
    model = Scripted("Games lead at 105, well ahead of books at 40.5.")
    turn = take_turn(
        model, "what does this reveal", manifest, con, workspace, [], router=router
    )
    assert (turn.shortcut, turn.reply_verified) == (True, True)
    assert turn.reply == "Games lead at 105, well ahead of books at 40.5."
    assert [(run.op, run.chart) for run in turn.runs] == [("discuss", CHART)]
    sent = model.sent(0)
    assert "which the user is asking about" in sent and "games | 105.0" in sent


def test_the_planner_can_answer_from_a_chart_too(manifest, con):
    workspace = Workspace(charts=[CHART])
    model = Scripted(
        {"actions": [{"op": "discuss", "chart_id": "c1"}]}, "Toys are lowest at 35."
    )
    turn = take_turn(model, "which is lowest", manifest, con, workspace, [])
    assert (turn.reply, turn.runs[0].op) == ("Toys are lowest at 35.", "discuss")
    assert workspace.charts == [CHART]


def test_a_series_chart_is_read_out_one_row_for_each_x(manifest, con):
    monthly = Chart.model_validate(
        {
            "id": "c1",
            "title": "Monthly sales",
            "sql": "SELECT month(ordered) AS month, category, sum(amount) AS total"
            " FROM orders GROUP BY 1, 2 ORDER BY 1, 2",
            "spec": {"kind": "line", "x": "month", "y": "total", "series": "category"},
        }
    )
    router = Fixed(Shortcut("discuss", ("c1",)))
    model = Scripted("Games led in January at 60.")
    workspace = Workspace(charts=[monthly])
    take_turn(model, "who led in January", manifest, con, workspace, [], router=router)
    sent = model.sent(0)
    assert "month | books (total) | games (total) | toys (total) | highest" in sent
    assert "1 | 12.5 | 60.0 |  | games | books" in sent and "3 rows" in sent


def test_a_reply_that_corrects_itself_is_written_again(manifest, con):
    workspace = Workspace(charts=[CHART])
    router = Fixed(Shortcut("discuss", ("c1",)))
    model = Scripted(
        "Books lead at 40.5. Wait, that's wrong: games lead at 105.",
        "Games lead at 105.",
    )
    turn = take_turn(model, "who leads", manifest, con, workspace, [], router=router)
    assert turn.reply == "Games lead at 105."
    assert "corrects itself" in model.sent(1)
