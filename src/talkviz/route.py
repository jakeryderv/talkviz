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
from talkviz.workspace import Chart, SetLens, Workspace

DEFAULT_ROUTER = "typesafe/jev-1.14"  # pinned: CONFIDENT was chosen on this version
URL = "https://openrouter.ai/api/alpha/decisions"
CONFIDENT = 0.7
"""Least confidence to act on. In testing every wrong answer was at or below 0.6."""
TIMEOUT = 2.0  # seconds; slower than this and the planner might as well go first
MAX_FAILURES = 3  # in a row, after which the router stops being asked
MAX_AXIS = 60  # x values offered as zoom ends; longer axes go to the planner


@dataclass(frozen=True)
class Shortcut:
    """A request that needs no planning."""

    op: Literal["set_view", "show_chart", "navigate", "discuss"]
    chart_ids: tuple[str, ...]
    """The charts meant, in the order they were created. One or more for
    show_chart; one otherwise."""
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

    def navigate(
        self, question: str, chart: Chart, xs: list[str], series: list[str]
    ) -> SetLens | None:
        """How a "navigate" shortcut moves around `chart`, or None to plan it.

        `xs` and `series` are the chart's x values and series names, as
        charts.axis() gives them.
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
            answers = response.json()["answers"]
            found = _decide(answers, workspace, focus or workspace.charts[-1].id)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            self.failures += 1
            return None
        self.failures = 0
        return found

    def navigate(
        self, question: str, chart: Chart, xs: list[str], series: list[str]
    ) -> SetLens | None:
        if not xs or len(xs) > MAX_AXIS or self.failures >= MAX_FAILURES:
            return None
        hidden = chart.lens.hidden if chart.lens else []
        details = {
            "title": chart.title,
            "drawn_as": chart.shown,
            "axis": getattr(chart.spec, "x", None),
            "axis_values": xs,
            "series": series,
            "hidden_series": hidden,
        }
        body = {
            "model": self.model,
            "state": {"chart": details, "user_says": question},
            "questions": _lens_questions(xs, series),
        }
        try:
            response = self.client.post(URL, json=body, headers=self.headers)
            response.raise_for_status()
            found = _move(response.json()["answers"], chart.id, series)
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
    for chart in workspace.charts:
        entry: dict = {"number": chart.label, "id": chart.id, "title": chart.title}
        if chart.parent:
            entry["sub_chart_of"] = chart.parent.removeprefix("c")
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
                f"Is the user referring to chart {chart.label}, titled {chart.title!r}?"
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
        for chart in workspace.charts
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
                    "Asks to change what an existing chart shows: filter it to"
                    " some rows, change its grouping, measure, how many items,"
                    " or its title."
                ),
                "navigate": (
                    "Asks only to move around an existing chart without changing"
                    " its data: zoom in or out, focus on a range, hide or bring"
                    " back one of its series, or reset the zoom."
                ),
                "redraw": (
                    "Asks only to draw an existing chart's same data as a"
                    " different kind of chart or as a table."
                ),
                "show": (
                    "Asks only to put one or more existing charts on screen"
                    " again, unchanged, alone or side by side."
                ),
                "ask": (
                    "Asks a question about what an existing chart shows, to be"
                    " answered in words from that chart as it is: what it"
                    " reveals or means, what stands out, a summary, which is"
                    " highest or lowest, how something changed."
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


_SHORTCUTS = ("redraw", "show", "navigate", "ask")


def _decide(answers: dict, workspace: Workspace, focus: str) -> Shortcut | None:
    route = answers["route"]
    if route["confidence"] < CONFIDENT or route["choice"] not in _SHORTCUTS:
        return None
    meant = {chart.id: answers[chart.id]["noul"] for chart in workspace.charts}
    charts = tuple(chart for chart, yes in meant.items() if yes >= CONFIDENT)
    if route["choice"] == "navigate":
        # "Hide Ford" seldom names a chart: it means the one on screen. What
        # it asks for is then checked against that chart's own values.
        if len(charts) > 1:
            return None
        return Shortcut("navigate", charts or (focus,))
    if route["choice"] == "ask":
        # Likewise "what does this reveal" is about the chart on screen.
        return Shortcut("discuss", charts or (focus,))
    if any(1 - CONFIDENT < yes < CONFIDENT for yes in meant.values()):
        return None  # unsure about a chart
    if not charts:
        return None
    if route["choice"] == "show":
        return Shortcut("show_chart", charts)
    kind = answers["kind"]
    known = kind["choice"] in KIND_NAMES and kind["confidence"] >= CONFIDENT
    if len(charts) != 1 or not known:
        return None
    return Shortcut("set_view", charts, kind["choice"])


def _lens_questions(xs: list[str], series: list[str]) -> dict:
    ends = {"none": "They gave no range."}
    named = {
        f"s{index}": {
            "type": "noul",
            "instructions": f"Does the user name the series {name!r}?",
            "criteria": {"true": "They name it.", "false": "They do not name it."},
        }
        for index, name in enumerate(series)
    }
    return {
        "action": {
            "type": "choice",
            "instructions": "How does the user want to move around this chart?",
            "criteria": {
                "zoom": "Zoom in on, or focus on, a range along the bottom axis.",
                "hide": "Hide one or more of its series.",
                "show": (
                    "Bring back one or more hidden series, keeping the rest as"
                    " they are."
                ),
                "zoom_out": "Zoom back out to the whole axis.",
                "reset": "Undo all zooming and hiding: show everything again.",
                "none": "None of these.",
            },
        },
        "from": {
            "type": "choice",
            "instructions": "Where along the axis should the zoomed range start?",
            "criteria": {**{x: f"Start at {x}." for x in xs}, **ends},
        },
        "to": {
            "type": "choice",
            "instructions": "Where along the axis should the zoomed range end?",
            "criteria": {**{x: f"End at {x}." for x in xs}, **ends},
        },
        **named,
    }


def _move(answers: dict, chart_id: str, series: list[str]) -> SetLens | None:
    action = answers["action"]
    if action["confidence"] < CONFIDENT:
        return None
    if action["choice"] == "reset":
        return SetLens(op="set_lens", chart_id=chart_id, reset=True)
    if action["choice"] == "zoom_out":
        return SetLens(op="set_lens", chart_id=chart_id, zoom_out=True)
    if action["choice"] == "zoom":
        start, end = answers["from"], answers["to"]
        sure = min(start["confidence"], end["confidence"]) >= CONFIDENT
        if not sure or "none" in (start["choice"], end["choice"]):
            return None
        return SetLens(
            op="set_lens", chart_id=chart_id, start=start["choice"], end=end["choice"]
        )
    if action["choice"] not in ("hide", "show"):
        return None
    said = {name: answers[f"s{index}"]["noul"] for index, name in enumerate(series)}
    names = [name for name, yes in said.items() if yes >= CONFIDENT]
    if not names or any(1 - CONFIDENT < yes < CONFIDENT for yes in said.values()):
        return None
    if action["choice"] == "hide":
        return SetLens(op="set_lens", chart_id=chart_id, hide=names)
    return SetLens(op="set_lens", chart_id=chart_id, show=names)
