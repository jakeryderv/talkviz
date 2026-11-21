"""One turn of a conversation: a question in, charts and a spoken reply out."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import duckdb

from talkviz import llm
from talkviz.charts import axis, fit_lens, views, why_not
from talkviz.llm import ChartRun, Exchange, Model, Plan, Planned, Rejected
from talkviz.manifest import Manifest
from talkviz.route import Router, Seen, Shortcut
from talkviz.sql import QueryRejected
from talkviz.workspace import (
    Chart,
    Discuss,
    Lens,
    SetView,
    ShowChart,
    Workspace,
    run_chart,
)


@dataclass
class Turn:
    question: str
    plan: Plan
    runs: list[ChartRun]
    reply: str
    reply_verified: bool
    seconds: dict[str, float]
    """Time spent in each stage: route, plan (including its queries), speak."""
    messages: dict[str, bytes]
    """The raw model exchanges for each stage, as JSON."""
    shortcut: bool = False
    rejected: list[Rejected] = field(default_factory=list)
    """Draft replies that failed the number check."""
    caveats: list[int] = field(default_factory=list)
    """The dataset caveats the reply mentions, by number."""
    """The router handled the turn, so no language model was called."""


def take_turn(
    model: Model,
    question: str,
    manifest: Manifest,
    con: duckdb.DuckDBPyConnection,
    workspace: Workspace,
    history: list[Exchange],
    speaker: Model | None = None,
    router: Router | None = None,
    focus: str | None = None,
    seen: dict[str, Seen] | None = None,
    stage: Callable[[str], None] = lambda name: None,
    as_of: str | None = None,
) -> Turn:
    """Plan and run the charts, write the reply, and update workspace and history.

    `model` plans the charts; `speaker`, if given, writes the reply instead.
    `router`, if given, sees the message first and handles requests that need
    no planning; `focus` tells it which chart is on screen and `seen` what
    each chart's latest result looks like. `stage` is told when each stage
    begins: "route", "plan", then "speak". `as_of` is the date the data runs to.
    """
    began = time.perf_counter()
    planned, misfit = None, None
    if router is not None:
        stage("route")
        found = _shortcut(router, question, manifest, con, workspace, focus, seen or {})
        if isinstance(found, Planned):
            planned = found
        else:
            misfit = found
    started = time.perf_counter()
    shortcut = planned is not None
    if planned is None:
        stage("plan")
        planned = llm.plan(
            model, question, manifest, con, workspace, history, misfit, focus
        )
    planned_at = time.perf_counter()
    fixed = llm.view_reply(planned.runs)
    if fixed is not None:
        spoken = llm.Spoken(fixed, verified=True, messages=b"[]")
    else:
        stage("speak")
        spoken = llm.speak(
            speaker or model, question, manifest, planned, history, as_of=as_of
        )
    done = time.perf_counter()
    for run in planned.runs:
        workspace.apply(run.chart)
    history.append(Exchange(question, spoken.text, tuple(spoken.caveats)))
    return Turn(
        question=question,
        plan=planned.plan,
        runs=planned.runs,
        reply=spoken.text,
        reply_verified=spoken.verified,
        seconds={
            "route": started - began,
            "plan": planned_at - started,
            "speak": done - planned_at,
        },
        messages={"plan": planned.messages, "speak": spoken.messages},
        shortcut=shortcut,
        rejected=spoken.rejected,
        caveats=spoken.caveats,
    )


_UNDO = re.compile(
    r"\b(?:undo|revert|go(?:ing)? back|put (?:it|that|this) back|change (?:it|that)"
    r" back|how it was|as it was|(?:previous|earlier|first|last|original) version"
    r"|version \d+)\b",
    re.IGNORECASE,
)


def _shortcut(
    router: Router,
    question: str,
    manifest: Manifest,
    con: duckdb.DuckDBPyConnection,
    workspace: Workspace,
    focus: str | None,
    seen: dict[str, Seen],
) -> Planned | tuple[Chart, str, str] | None:
    """The turn as the router sees it, or None if it should be planned.

    Everything the router says is checked here: the chart must exist, its
    query must run, and a view must fit the result. If the view does not fit,
    the planner takes the turn and is told so: the chart, the kind, and why.
    It can then change the query or explain what cannot be done.
    """
    # A chart named by a label nobody has, like "1b", is the planner's to
    # explain; the router only knows the charts there are.
    labels = {chart.label for chart in workspace.charts}
    if set(re.findall(r"\b\d+[a-z]\b", question.lower())) - labels:
        return None
    # Undoing a change is the planner's too: the router has no such answer,
    # and takes "go back to the first version" for showing a chart again.
    if _UNDO.search(question):
        return None
    found = router.shortcut(question, workspace, focus, seen)
    if found is None or not 0 < len(found.chart_ids) <= llm.MAX_ACTIONS:
        return None
    charts = [c for c in map(workspace.get, found.chart_ids) if c is not None]
    if len(charts) != len(found.chart_ids):
        return None
    try:
        results = [run_chart(con, manifest, workspace, chart) for chart in charts]
    except (QueryRejected, ValueError):
        return None
    # "Show everything again" about the chart already on screen, zoomed or
    # with series hidden, is a request to undo that, not to show it.
    again = found.op == "show_chart" and found.chart_ids == (focus,)
    if again and charts[0].lens is not None:
        found = Shortcut("navigate", found.chart_ids)
    if found.op in ("show_chart", "discuss"):
        # Showing needs no model at all. A question about a chart skips the
        # planner and goes straight to the reply, written from its rows.
        shows: list[ShowChart | Discuss] = [
            ShowChart(op="show_chart", chart_id=chart.id)
            if found.op == "show_chart"
            else Discuss(op="discuss", chart_id=chart.id)
            for chart in charts
        ]
        runs = [
            ChartRun(workspace.resolve(show), result, show.op)
            for show, result in zip(shows, results, strict=True)
        ]
        return Planned(Plan(actions=list(shows)), runs, b"[]")
    if len(charts) != 1:
        return None
    chart, result = charts[0], results[0]
    if found.op == "navigate":
        move = router.navigate(question, chart, *axis(chart.spec, result))
        if move is None:
            return None
        moved = workspace.resolve(move)
        if moved.lens is not None:
            lens = moved.lens
            try:
                fitted = fit_lens(chart.spec, result, lens.start, lens.end, lens.hidden)
            except ValueError:
                return None
            start, end, hidden = fitted
            moved = moved.model_copy(
                update={"lens": Lens(start=start, end=end, hidden=hidden)}
            )
        return Planned(Plan(actions=[move]), [ChartRun(moved, result, move.op)], b"[]")
    if found.kind in [view.kind for view in views(chart.spec, result)]:
        action = SetView(op="set_view", chart_id=chart.id, kind=found.kind)
    else:
        assert found.kind is not None
        return chart, found.kind, why_not(chart.spec, found.kind, result)
    run = ChartRun(workspace.resolve(action), result, action.op)
    return Planned(Plan(actions=[action]), [run], b"[]")
