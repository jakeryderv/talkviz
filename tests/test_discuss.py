"""Asking about a chart: answered in words from its rows; nothing changes."""

from test_lens import routed
from test_llm import TOTALS, Scripted, create
from test_route import Fixed

from talkviz import llm
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


# Games lead months 1 and 3 and toys month 2; books are lowest in all three.
MONTHLY = Chart.model_validate(
    {
        "id": "c1",
        "title": "Monthly sales",
        "sql": "SELECT month(ordered) AS month, category, sum(amount) AS total"
        " FROM orders GROUP BY 1, 2 ORDER BY 1, 2",
        "spec": {"kind": "line", "x": "month", "y": "total", "series": "category"},
    }
)


def told(model, call) -> str:
    """The instructions the model had for its call-th reply."""
    return str(getattr(model.seen[call][0], "instructions", ""))


def ask(model, question, manifest, con, history=None):
    """A question about the monthly chart, answered by `model` without a plan."""
    router = Fixed(Shortcut("discuss", ("c1",)))
    workspace = Workspace(charts=[MONTHLY])
    history = [] if history is None else history
    return take_turn(model, question, manifest, con, workspace, history, router=router)


def test_a_series_chart_is_read_out_one_row_for_each_x(manifest, con):
    model = Scripted("Games led in January at 60.")
    ask(model, "who led in January", manifest, con)
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


def test_the_reply_model_is_given_who_led_when_and_each_series_extremes(manifest, con):
    model = Scripted("Games led in January at 60.")
    ask(model, "who led in January", manifest, con)
    sent = model.sent(0)
    assert "Highest total: games 1, toys 2, games 3." in sent
    assert "Lowest total: books 1 to 3." in sent
    assert (
        "books: starts 12.5 at 1, ends 8.0 at 3, peak 20.0 at 2, low 8.0 at 3." in sent
    )


def test_a_false_claim_that_one_series_always_leads_is_written_again(manifest, con):
    model = Scripted("Games had the highest sales every month.", "Games led twice.")
    turn = ask(model, "who leads", manifest, con)
    assert turn.reply == "Games led twice."
    assert "Highest: games 1, toys 2, games 3." in model.sent(1)


def test_true_claims_about_every_point_are_kept(manifest, con):
    for claim in (
        "Books were the lowest every month.",
        "Games were above books in every month they both sold.",
        "Games led every month except month 2.",
        "Sales moved around every month.",
    ):
        model = Scripted(claim)
        assert ask(model, "what stands out", manifest, con).reply == claim


def test_a_claim_that_one_series_is_always_above_another_is_checked():
    cells = {
        (2023, "ford"): 90,
        (2023, "kia"): 70,
        (2024, "ford"): 60,
        (2024, "kia"): 80,
    }
    pivot = llm.Pivot("year", "fires", [2023, 2024], ["ford", "kia"], cells, True)
    [problem] = llm._overclaims("Ford was above Kia in every year.", [pivot])
    assert (
        problem
        == "ford is above kia at 2023 (90 to 70) and below it at 2024 (60 to 80)."
    )
    assert llm._overclaims("Ford was above Kia in 2023.", [pivot]) == []


def chart(model, manifest, con, history, workspace=None):
    """A turn that makes a chart, which is when a caveat may be added."""
    workspace = Workspace() if workspace is None else workspace
    return take_turn(model, "sales by category?", manifest, con, workspace, history)


def test_a_caveat_is_said_once_in_a_conversation(manifest, con):
    history, workspace = [], Workspace()
    model = Scripted(
        {"actions": [create()]},
        "Games lead at 105.\nCaveat 1: Refunds are left out.",
        {"actions": [create(title="Sales again")]},
        "Games still lead at 105.",
    )
    first = chart(model, manifest, con, history, workspace)
    assert first.reply == "Games lead at 105. Refunds are left out."
    assert (first.caveats, history[0].caveats) == ([1], (1,))
    assert "1. Refunds are not included." in told(model, 1)
    second = chart(model, manifest, con, history, workspace)
    assert second.caveats == []
    instructions = told(model, 3)
    assert "1. Refunds" not in instructions and "Add no caveat line" in instructions
    assert "- Refunds are not included." in instructions


def test_no_caveat_is_offered_when_a_turn_brings_no_new_results(manifest, con):
    model = Scripted("Games led in January at 60.")
    ask(model, "who led in January", manifest, con)
    assert "Add no caveat line" in told(model, 0)


def test_a_reply_gets_one_caveat(manifest, con):
    model = Scripted(
        {"actions": [create()]},
        "Games lead.\nCaveat 1: No refunds.\nCaveat 1: Also this.",
    )
    turn = chart(model, manifest, con, [])
    assert (turn.reply, turn.caveats) == ("Games lead. No refunds.", [1])


def test_which_caveat_was_said_is_judged_from_its_words(manifest, con):
    caveats = ["Refunds are not included.", "Orders placed by phone are missing."]
    manifest = manifest.model_copy(update={"caveats": caveats})
    model = Scripted(
        {"actions": [create()]}, "Games lead.\nCaveat 1: Phone orders are missing."
    )
    assert chart(model, manifest, con, []).caveats == [2]
