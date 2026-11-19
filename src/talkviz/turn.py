"""One turn of a conversation: a question in, charts and a spoken reply out."""

import time
from dataclasses import dataclass

import duckdb

from talkviz import llm
from talkviz.charts import views, why_not
from talkviz.llm import ChartRun, Exchange, Model, Plan, Planned
from talkviz.manifest import Manifest
from talkviz.route import Router, Seen
from talkviz.sql import QueryRejected, check_and_run
from talkviz.workspace import Chart, SetView, ShowChart, Workspace


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
) -> Turn:
    """Plan and run the charts, write the reply, and update workspace and history.

    `model` plans the charts; `speaker`, if given, writes the reply instead.
    `router`, if given, sees the message first and handles requests that need
    no planning; `focus` tells it which chart is on screen and `seen` what
    each chart's latest result looks like.
    """
    began = time.perf_counter()
    planned, misfit = None, None
    if router is not None:
        found = _shortcut(router, question, manifest, con, workspace, focus, seen or {})
        if isinstance(found, Planned):
            planned = found
        else:
            misfit = found
    started = time.perf_counter()
    shortcut = planned is not None
    if planned is None:
        planned = llm.plan(
            model, question, manifest, con, workspace, history, misfit=misfit
        )
    planned_at = time.perf_counter()
    fixed = llm.view_reply(planned.runs)
    if fixed is not None:
        spoken = llm.Spoken(fixed, verified=True, messages=b"[]")
    else:
        spoken = llm.speak(speaker or model, question, manifest, planned, history)
    done = time.perf_counter()
    for run in planned.runs:
        workspace.apply(run.chart)
    history.append(Exchange(question, spoken.text))
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
    found = router.shortcut(question, workspace, focus, seen)
    if found is None or not 0 < len(found.chart_ids) <= llm.MAX_ACTIONS:
        return None
    charts = [c for c in map(workspace.get, found.chart_ids) if c is not None]
    if len(charts) != len(found.chart_ids):
        return None
    try:
        results = [check_and_run(con, chart.sql, manifest) for chart in charts]
    except QueryRejected:
        return None
    if found.op == "show_chart":
        shows = [ShowChart(op="show_chart", chart_id=chart.id) for chart in charts]
        runs = [
            ChartRun(workspace.resolve(show), result, show.op)
            for show, result in zip(shows, results, strict=True)
        ]
        return Planned(Plan(actions=list(shows)), runs, b"[]")
    if len(charts) != 1:
        return None
    chart, result = charts[0], results[0]
    if found.kind in [view.kind for view in views(chart.spec, result)]:
        action = SetView(op="set_view", chart_id=chart.id, kind=found.kind)
    else:
        assert found.kind is not None
        return chart, found.kind, why_not(chart.spec, found.kind, result)
    run = ChartRun(workspace.resolve(action), result, action.op)
    return Planned(Plan(actions=[action]), [run], b"[]")
