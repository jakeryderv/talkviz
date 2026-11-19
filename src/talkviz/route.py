"""A fast first look at each message, before the planner.

Some requests need no planning: "make that a donut" or "show charts 1 and 2
side by side" only pick among charts and kinds that already exist. A decision
model answers typed questions about the message in a fraction of a second:
what is being asked, which kind was named, and, for each chart, whether it is
meant. When it is confident the request is one of those, the turn skips the
language model.
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
    chart_ids: tuple[str, ...]
    """The charts meant, in the order they were created. One for set_view;
    one or more for show_chart."""
    kind: str | None = None
    """The kind to draw the chart as, for set_view."""


@dataclass(frozen=True)
class Seen:
    """What a chart's latest result looks like, to help tell charts apart."""

    columns: list[str]
    views: list[str]
    """The other kinds it can be drawn as."""


class Router(Protocol):
    def shortcut(
        self,
        question: str,
        workspace: Workspace,
        focus: str | None,
        seen: dict[str, Seen],
    ) -> Shortcut | None:
        """The shortcut the message asks for, or None to plan it as usual.

        `focus` is the id of the chart on screen. `seen` describes the latest
        result of each chart that has one, by chart id.
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
        self,
        question: str,
        workspace: Workspace,
        focus: str | None,
        seen: dict[str, Seen],
    ) -> Shortcut | None:
        if not workspace.charts or self.failures >= MAX_FAILURES:
            return None
        body = {
            "model": self.model,
            "state": _state(question, workspace, focus, seen),
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


def _state(
    question: str, workspace: Workspace, focus: str | None, seen: dict[str, Seen]
) -> dict:
    charts = []
    for number, chart in enumerate(workspace.charts, start=1):
        entry: dict = {"number": number, "id": chart.id, "title": chart.title}
        entry["drawn_as"] = chart.shown
        if chart.id in seen:
            entry["columns"] = seen[chart.id].columns
            entry["can_also_be_drawn_as"] = seen[chart.id].views
        charts.append(entry)
    return {
        "charts_in_order_created": charts,
        "chart_on_screen": focus or workspace.charts[-1].id,
        "user_says": question,
    }


def _questions(workspace: Workspace) -> dict:
    kinds = {kind: f"{name.capitalize()}." for kind, name in KIND_NAMES.items()}
    kinds |= {"donut": "A donut or pie chart.", "table": "A table, or the raw numbers."}
    # One yes or no per chart, since a message can mean several charts.
    meant = {
        chart.id: {
            "type": "noul",
            "instructions": (
                f"Is the user referring to chart {number}, titled {chart.title!r}?"
            ),
            "criteria": {
                "true": (
                    "They mean this existing chart, by its number, its title, a"
                    " description of it, or as the chart on screen"
                    ' ("it", "that").'
                ),
                "false": "They do not mean this chart.",
            },
        }
        for number, chart in enumerate(workspace.charts, start=1)
    }
    return {
        "route": {
            "type": "choice",
            "instructions": "What is the user asking the charting assistant to do?",
            "criteria": {
                "new": (
                    "Asks a new question about the data that needs a new chart or"
                    " a new query."
                ),
                "change": (
                    "Asks to change what an existing chart shows: its filter, time"
                    " range, grouping, measure, how many items, or its title."
                ),
                "redraw": (
                    "Asks only to draw an existing chart's same data as a"
                    " different kind of chart or as a table."
                ),
                "show": (
                    "Asks only to see one or more existing charts again,"
                    " unchanged, alone or side by side."
                ),
                "none": (
                    "Not a request about this dataset or its charts: small talk,"
                    " off topic, or too vague to act on."
                ),
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
        **meant,
    }


def _decide(answers: dict, workspace: Workspace) -> Shortcut | None:
    route = answers["route"]
    if route["confidence"] < CONFIDENT or route["choice"] not in ("redraw", "show"):
        return None
    meant = {chart.id: answers[chart.id]["noul"] for chart in workspace.charts}
    if any(1 - CONFIDENT < yes < CONFIDENT for yes in meant.values()):
        return None  # unsure about a chart
    charts = tuple(chart for chart, yes in meant.items() if yes >= CONFIDENT)
    if not charts:
        return None
    if route["choice"] == "show":
        return Shortcut("show_chart", charts)
    kind = answers["kind"]
    known = kind["choice"] in KIND_NAMES and kind["confidence"] >= CONFIDENT
    if len(charts) != 1 or not known:
        return None
    return Shortcut("set_view", charts, kind["choice"])
