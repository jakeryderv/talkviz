"""A fast first look at each message, before the planner.

Some requests need no planning: "make that a donut" or "show the first chart
again" only name a chart and perhaps a kind. A decision model answers three
typed questions about the message in a fraction of a second, and when it is
confident the request is one of those, the turn skips the language model.
Anything else, and any doubt or failure, goes to the planner as before, so a
wrong answer here costs a little time, not a wrong action.

Uses Jev through OpenRouter's Decisions API, which is in alpha.
"""

import os
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx

from talkviz.charts import KIND_NAMES
from talkviz.workspace import Workspace

DEFAULT_ROUTER = "typesafe/jev-1.14"  # pinned: CONFIDENT was chosen on this version
URL = "https://openrouter.ai/api/alpha/decisions"
CONFIDENT = 0.7
"""Least confidence to act on. In testing every wrong answer was at or below 0.6."""
TIMEOUT = 2.0  # seconds; slower than this and the planner might as well go first
MAX_FAILURES = 3  # in a row, after which the router stops being asked


@dataclass(frozen=True)
class Shortcut:
    """A request that needs no planning."""

    op: Literal["set_view", "show_chart"]
    chart_id: str
    kind: str | None = None
    """The kind to draw the chart as, for set_view."""


class Router(Protocol):
    def shortcut(
        self, question: str, workspace: Workspace, focus: str | None
    ) -> Shortcut | None:
        """The shortcut the message asks for, or None to plan it as usual.

        `focus` is the id of the chart on screen.
        """
        ...


class Jev:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ROUTER,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.client = client or httpx.Client(timeout=TIMEOUT)
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.failures = 0

    def shortcut(
        self, question: str, workspace: Workspace, focus: str | None
    ) -> Shortcut | None:
        if not workspace.charts or self.failures >= MAX_FAILURES:
            return None
        body = {
            "model": self.model,
            "state": _state(question, workspace, focus),
            "questions": _questions(workspace),
        }
        try:
            response = self.client.post(URL, json=body, headers=self.headers)
            response.raise_for_status()
            found = _decide(response.json()["answers"], workspace)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            self.failures += 1
            return None
        self.failures = 0
        return found


def jev() -> Jev | None:
    """The configured router: TALKVIZ_ROUTER names the model, or "off" for
    none. None too if there is no OPENROUTER_API_KEY."""
    model = os.environ.get("TALKVIZ_ROUTER") or DEFAULT_ROUTER
    key = os.environ.get("OPENROUTER_API_KEY")
    if model == "off" or not key:
        return None
    return Jev(key, model)


def _state(question: str, workspace: Workspace, focus: str | None) -> dict:
    charts = [
        {"id": chart.id, "title": chart.title, "drawn_as": chart.shown}
        for chart in workspace.charts
    ]
    return {
        "charts_in_order_created": charts,
        "chart_on_screen": focus or workspace.charts[-1].id,
        "user_says": question,
    }


def _questions(workspace: Workspace) -> dict:
    charts = {c.id: f"The chart titled {c.title!r}." for c in workspace.charts}
    kinds = {kind: f"{name.capitalize()}." for kind, name in KIND_NAMES.items()}
    kinds |= {"donut": "A donut or pie chart.", "table": "A table, or the raw numbers."}
    return {
        "route": {
            "type": "choice",
            "instructions": "What is the user asking the charting assistant to do?",
            "criteria": {
                "new": "Asks a new question about the data that needs a new chart.",
                "change": (
                    "Asks to change what an existing chart shows: its filter, time"
                    " range, grouping, measure, how many items, or its title."
                ),
                "redraw": (
                    "Asks only to draw an existing chart's same data as a"
                    " different kind of chart or as a table."
                ),
                "show": "Asks only to see an existing chart again, unchanged.",
                "none": (
                    "Not a request about this dataset or its charts: small talk,"
                    " off topic, or too vague to act on."
                ),
            },
        },
        "chart": {
            "type": "choice",
            "instructions": "Which existing chart is the user referring to?",
            "criteria": {
                **charts,
                "none": "No existing chart: a new question, or not about a chart.",
            },
        },
        "kind": {
            "type": "choice",
            "instructions": "Which kind of chart did the user name?",
            "criteria": {
                **kinds,
                "none": "They did not ask for a particular kind of chart.",
            },
        },
    }


def _decide(answers: dict, workspace: Workspace) -> Shortcut | None:
    def sure(question: str) -> str | None:
        answer = answers[question]
        return answer["choice"] if answer["confidence"] >= CONFIDENT else None

    route, chart = sure("route"), sure("chart")
    if route not in ("redraw", "show") or workspace.get(chart or "") is None:
        return None
    assert chart is not None
    if route == "show":
        return Shortcut("show_chart", chart)
    kind = sure("kind")
    if kind is None or kind not in KIND_NAMES:
        return None
    return Shortcut("set_view", chart, kind)
