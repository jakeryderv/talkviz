"""Asking about a chart: answered in words from its rows; nothing changes."""

from test_lens import routed
from test_llm import TOTALS, Scripted
from test_route import Fixed

from talkviz import llm
from talkviz.route import Shortcut, _decide
from talkviz.sql import QueryResult, ResultColumn
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


def test_the_limits_of_the_data_are_left_to_the_screen(manifest, con):
    model = Scripted("Games led in January at 60.")
    ask(model, "who led in January", manifest, con)
    instructions = told(model, 0)
    assert "Refunds" not in instructions
    assert "shown to the user beside every chart" in instructions


def test_a_question_asking_for_a_judgement_is_given_the_limits(manifest, con):
    history = [llm.Exchange("who led", "Games led. Remember refunds are left out.")]
    model = Scripted("Games did best. Keep in mind that refunds are not included.")
    turn = ask(model, "which category did best", manifest, con, history)
    assert "- Refunds are not included." in told(model, 0)
    # And its reply keeps the limit, though one was said before.
    assert turn.reply == "Games did best. Keep in mind that refunds are not included."


def test_a_long_reply_is_asked_for_again_once(manifest, con):
    long = "Games led in January. " + "It was a good month for games. " * 12
    model = Scripted(long, "Games led in January at 60.")
    assert ask(model, "who led", manifest, con).reply == "Games led in January at 60."
    assert "too long" in model.sent(1)
    # A second long one is let through, not looped on.
    model = Scripted(long, long)
    assert ask(model, "who led", manifest, con).reply == long.strip()


CAVEATS = [
    (
        "Complaint counts are not defect rates. There is no count of vehicles on the"
        " road, so a make with more complaints is not necessarily less safe."
    ),
    (
        "Complaints are unverified consumer reports. Crash, fire, injury, and death"
        " figures are what the consumer reported."
    ),
]


def test_reminders_are_told_from_sentences_about_the_results():
    for reminder in (
        "Keep in mind that Ford sells a lot of vehicles.",
        "Complaint counts are not defect rates, so more complaints is not less safe.",
        "These are unverified consumer complaints, not confirmed reports.",
        "That 2026 figure is partial, so the full-year number would be higher.",
        "Note that 2026 is only partway through the year, so it runs low.",
        "The latest year is only partway through, so it looks lower.",
    ):
        assert llm._is_reminder(reminder, CAVEATS), reminder
    for finding in (
        "Crash is the most common at about 96,700 complaints, then injury at 63,600.",
        "Ford's 2026 figure of about 148 is partial.",
        "Kia stays above Ford in every year since 2022.",
        "Fire complaints and crash complaints moved together.",
    ):
        assert not llm._is_reminder(finding, CAVEATS), finding


def test_a_reminder_is_said_once_in_a_conversation(manifest, con):
    history = []
    model = Scripted(
        "Games led at 60. Keep in mind that refunds are not included.",
        "Toys led. Remember that refunds are not included. Books trailed.",
        "Refunds are not included, so I can't say. Remember that.",
    )
    first = ask(model, "who led in January", manifest, con, history)
    assert first.reply == "Games led at 60. Keep in mind that refunds are not included."
    second = ask(model, "and in February", manifest, con, history)
    assert second.reply == "Toys led. Books trailed."
    # Asked about the latest period, that it is incomplete stays in.
    partial = ["Up to 60.", "That year is incomplete."]
    assert list(llm._unreminded(partial, [], True)) == ["Up to 60."]
    assert list(llm._unreminded(partial, [], True, recent=True)) == partial
    # A reply that opens with a limit is answering with it, and keeps it.
    third = ask(model, "what were net sales", manifest, con, history)
    assert third.reply == "Refunds are not included, so I can't say."


def test_the_partial_year_is_mentioned_to_the_model_once_unless_asked_about():
    cells = {(2023, "a"): 9, (2023, "b"): 7, (2024, "a"): 6, (2024, "b"): 8}
    result = QueryResult(
        columns=[
            ResultColumn("year", "BIGINT"),
            ResultColumn("make", "VARCHAR"),
            ResultColumn("fires", "BIGINT"),
        ],
        rows=[(x, name, value) for (x, name), value in cells.items()],
        truncated=False,
        seconds=0.0,
    )
    spec = {"kind": "line", "x": "year", "y": "fires", "series": "make"}
    chart = Chart.model_validate(
        {"id": "c1", "title": "Fires", "sql": "x", "spec": spec}
    )
    planned = llm.Planned(
        llm.Plan(actions=[]), [llm.ChartRun(chart, result, "discuss", "")], b""
    )
    said = [llm.Exchange("fires?", "A led. 2024 is incomplete.")]

    def prompt(question, history):
        return llm._speech_prompt(question, planned, history, "2024-10-20")

    assert "the data runs to" in prompt("fires by make", [])
    assert "the data runs to" not in prompt("who leads", said)
    assert "the data runs to" in prompt("did it drop in 2024", said)


def test_a_partial_last_year_is_given_as_a_fact_about_that_year():
    cells = {
        (2023, "ford"): 90,
        (2023, "kia"): 70,
        (2024, "ford"): 60,
        (2024, "kia"): 80,
    }
    pivot = llm.Pivot("year", "fires", [2023, 2024], ["ford", "kia"], cells, True)
    [partial] = [f for f in llm._facts(pivot, "2024-10-20") if "incomplete" in f]
    assert partial.startswith("2024 is incomplete: the data runs to 2024-10-20")
    for whole in ("2024-12-31", "2025-03-01", None, "v7"):
        assert not any("incomplete" in fact for fact in llm._facts(pivot, whole))


def test_undo_is_never_taken_by_the_fast_path(manifest, con):
    # The router would show the chart again; the planner is asked instead.
    router = Fixed(Shortcut("show_chart", ("c1",)))
    for said in ("undo that", "go back to the first version", "put it back how it was"):
        model = Scripted({"actions": [{"op": "show_chart", "chart_id": "c1"}]})
        workspace = Workspace(charts=[CHART])
        take_turn(model, said, manifest, con, workspace, [], router=router)
        assert len(model.seen) == 1, said
    model = Scripted()
    take_turn(model, "show it again", manifest, con, workspace, [], router=router)
    assert model.seen == []
