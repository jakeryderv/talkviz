import json

import httpx
import pytest
from test_llm import TOTALS, Scripted

from talkviz.llm import Exchange
from talkviz.route import Jev, Shortcut
from talkviz.turn import take_turn
from talkviz.workspace import Chart, Workspace

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

    def __init__(self, found: Shortcut | None) -> None:
        self.found = found

    def shortcut(self, question, workspace, focus) -> Shortcut | None:
        return self.found


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (
            {"route": answer("redraw"), "chart": answer("c1"), "kind": answer("donut")},
            Shortcut("set_view", "c1", "donut"),
        ),
        (
            {
                "route": answer("show"),
                "chart": answer("c1"),
                "kind": answer("none", 0.4),
            },
            Shortcut("show_chart", "c1"),
        ),
        # New questions and changes are the planner's.
        (
            {"route": answer("new"), "chart": answer("none"), "kind": answer("none")},
            None,
        ),
        (
            {"route": answer("change"), "chart": answer("c1"), "kind": answer("none")},
            None,
        ),
        # So is anything it is unsure of, or that names no real chart or kind.
        (
            {
                "route": answer("redraw", 0.6),
                "chart": answer("c1"),
                "kind": answer("donut"),
            },
            None,
        ),
        (
            {
                "route": answer("redraw"),
                "chart": answer("c1", 0.5),
                "kind": answer("donut"),
            },
            None,
        ),
        (
            {
                "route": answer("redraw"),
                "chart": answer("c1"),
                "kind": answer("donut", 0.5),
            },
            None,
        ),
        (
            {"route": answer("redraw"), "chart": answer("c1"), "kind": answer("none")},
            None,
        ),
        (
            {"route": answer("show"), "chart": answer("none"), "kind": answer("none")},
            None,
        ),
    ],
)
def test_only_confident_redraws_and_shows_are_shortcuts(answers, expected):
    router, sent = jev(answers)
    assert (
        router.shortcut("make it a donut", Workspace(charts=[CHART]), "c1") == expected
    )
    assert sent[0]["state"]["user_says"] == "make it a donut"
    assert sent[0]["state"]["chart_on_screen"] == "c1"
    assert set(sent[0]["questions"]["chart"]["criteria"]) == {"c1", "none"}


def test_the_router_is_not_asked_before_there_are_charts():
    router, sent = jev({})
    assert router.shortcut("sales by category?", Workspace(), None) is None
    assert sent == []


def test_a_failing_service_is_skipped_and_then_left_alone():
    workspace = Workspace(charts=[CHART])
    for router, sent in (jev(None, status=404), jev({"route": "unexpected"})):
        for _ in range(5):
            assert router.shortcut("make it a donut", workspace, "c1") is None
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
        router=Fixed(Shortcut("set_view", "c1", "donut")),
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
        router=Fixed(Shortcut("show_chart", "c1")),
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
        Shortcut("set_view", "c1", "line"),  # categories cannot be a line
        Shortcut("show_chart", "c9"),  # no such chart
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
