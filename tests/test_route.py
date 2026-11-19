import json

import httpx
import pytest
from test_llm import TOTALS, Scripted

from talkviz.llm import Exchange
from talkviz.route import Jev, Seen, Shortcut
from talkviz.turn import take_turn
from talkviz.workspace import Chart, SetLens, Workspace

CHART = Chart.model_validate(
    {
        "id": "c1",
        "title": "Sales by category",
        "sql": TOTALS,
        "spec": {"kind": "bar", "x": "category", "y": "total"},
    }
)


def answer(choice: str, confidence: float = 0.95) -> dict:
    return {"type": "choice", "choice": choice, "confidence": confidence}


def jev(answers: dict | None, status: int = 200) -> tuple[Jev, list[dict]]:
    """A router whose service returns `answers`, and the requests it was sent."""
    sent = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(status, json={"answers": answers})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    return Jev("key", client=client), sent


class Fixed:
    """A router that always gives the same answer."""

    def __init__(self, found: Shortcut | None, move: SetLens | None = None) -> None:
        self.found = found
        self.move = move

    def navigate(self, question, chart, xs, series) -> SetLens | None:
        self.offered = (xs, series)
        return self.move

    def shortcut(self, question, workspace, focus, seen) -> Shortcut | None:
        return self.found


OTHER = CHART.model_copy(update={"id": "c2", "title": "Orders by month"})


def answers(route, kind, c1, c2=0.02, sure=0.95):
    return {
        "route": answer(route, sure),
        "kind": answer(kind),
        "c1": {"type": "noul", "noul": c1},
        "c2": {"type": "noul", "noul": c2},
    }


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (answers("redraw", "donut", 0.97), Shortcut("set_view", ("c1",), "donut")),
        (answers("show", "none", 0.9), Shortcut("show_chart", ("c1",))),
        (answers("show", "none", 0.9, 0.8), Shortcut("show_chart", ("c1", "c2"))),
        # New questions and changes are the planner's.
        (answers("new", "none", 0.1), None),
        (answers("change", "none", 0.97), None),
        # So is anything it is unsure of, or that names no chart or kind.
        (answers("redraw", "donut", 0.97, sure=0.6), None),
        (answers("redraw", "donut", 0.5), None),
        (answers("show", "none", 0.9, 0.5), None),
        (answers("show", "none", 0.1), None),
        (answers("redraw", "none", 0.97), None),
        (answers("redraw", "donut", 0.9, 0.9), None),  # one kind, two charts
    ],
)
def test_only_confident_redraws_and_shows_are_shortcuts(given, expected):
    router, sent = jev(given)
    workspace = Workspace(charts=[CHART, OTHER])
    seen = {"c1": Seen(columns=["category", "total"], views=["donut", "table"])}
    assert router.shortcut("make it a donut", workspace, "c1", seen) == expected
    state = sent[0]["state"]
    assert (state["user_says"], state["chart_on_screen"]) == ("make it a donut", "c1")
    assert state["charts_in_order_created"][0] == {
        "number": 1,
        "id": "c1",
        "title": "Sales by category",
        "drawn_as": "bar",
        "columns": ["category", "total"],
        "can_also_be_drawn_as": ["donut", "table"],
    }
    assert set(sent[0]["questions"]) == {"route", "kind", "c1", "c2"}


def test_the_router_is_not_asked_before_there_are_charts():
    router, sent = jev({})
    assert router.shortcut("sales by category?", Workspace(), None, {}) is None
    assert sent == []


def test_a_failing_service_is_skipped_and_then_left_alone():
    workspace = Workspace(charts=[CHART])
    for router, sent in (jev(None, status=404), jev({"route": "unexpected"})):
        for _ in range(5):
            assert router.shortcut("make it a donut", workspace, "c1", {}) is None
        assert len(sent) == 3  # stops asking after three failures in a row


def test_a_shortcut_turn_calls_no_language_model(manifest, con):
    workspace, history = Workspace(charts=[CHART]), list[Exchange]()
    model = Scripted()  # any model call would fail: nothing is scripted
    turn = take_turn(
        model,
        "make it a donut",
        manifest,
        con,
        workspace,
        history,
        router=Fixed(Shortcut("set_view", ("c1",), "donut")),
    )
    assert (turn.shortcut, [run.op for run in turn.runs]) == (True, ["set_view"])
    assert turn.reply == "Here's Sales by category as a donut chart."
    assert (workspace.charts[0].view, workspace.charts[0].revision) == ("donut", 1)

    turn = take_turn(
        model,
        "show it again",
        manifest,
        con,
        workspace,
        history,
        router=Fixed(Shortcut("show_chart", ("c1",))),
    )
    assert turn.reply == "Here's Sales by category again."
    assert [e.reply for e in history] == [
        "Here's Sales by category as a donut chart.",
        "Here's Sales by category again.",
    ]


@pytest.mark.parametrize(
    "found",
    [
        None,
        Shortcut("set_view", ("c1",), "line"),  # categories cannot be a line
        Shortcut("show_chart", ("c9",)),  # no such chart
    ],
)
def test_what_the_router_cannot_settle_goes_to_the_planner(manifest, con, found):
    workspace = Workspace(charts=[CHART])
    model = Scripted(
        {"actions": [], "note": "Which chart?"}, "Which chart do you mean?"
    )
    turn = take_turn(
        model, "do the thing", manifest, con, workspace, [], router=Fixed(found)
    )
    assert (turn.shortcut, turn.reply) == (False, "Which chart do you mean?")
    # A view that does not fit is passed on, so the planner need not rediscover it.
    told = "They are asking to draw c1 ('Sales by category') as 'line'" in model.sent(0)
    assert told == (found is not None and found.kind == "line")


def test_several_charts_are_shown_together(manifest, con):
    workspace = Workspace(charts=[CHART, OTHER])
    found = Shortcut("show_chart", ("c1", "c2"))
    turn = take_turn(
        Scripted(),
        "both side by side",
        manifest,
        con,
        workspace,
        [],
        router=Fixed(found),
    )
    assert [(run.op, run.chart.id) for run in turn.runs] == [
        ("show_chart", "c1"),
        ("show_chart", "c2"),
    ]
    assert turn.reply == "Here are Sales by category and Orders by month, side by side."
