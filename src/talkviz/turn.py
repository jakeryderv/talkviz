"""One turn of a conversation: a question in, charts and a spoken reply out."""

import time
from dataclasses import dataclass

import duckdb

from talkviz import llm
from talkviz.charts import views, why_not
from talkviz.llm import ChartRun, Exchange, Model, Plan, Planned
from talkviz.manifest import Manifest
from talkviz.route import Router
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
) -> Turn:
    """Plan and run the charts, write the reply, and update workspace and history.

    `model` plans the charts; `speaker`, if given, writes the reply instead.
    `router`, if given, sees the message first and handles requests that need
    no planning; `focus` tells it which chart is on screen.
    """
    began = time.perf_counter()
    planned, misfit = None, None
    if router is not None:
        found = _shortcut(router, question, manifest, con, workspace, focus)
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
) -> Planned | tuple[Chart, str, str] | None:
    """The turn as the router sees it, or None if it should be planned.

    Everything the router says is checked here: the chart must exist, its
    query must run, and a view must fit the result. If the view does not fit,
    the planner takes the turn and is told so: the chart, the kind, and why.
    It can then change the query or explain what cannot be done.
    """
    found = router.shortcut(question, workspace, focus)
    chart = workspace.get(found.chart_id) if found else None
    if found is None or chart is None:
        return None
    try:
        result = check_and_run(con, chart.sql, manifest)
    except QueryRejected:
        return None
    if found.op == "show_chart":
        action = ShowChart(op="show_chart", chart_id=chart.id)
    elif found.kind in [view.kind for view in views(chart.spec, result)]:
        action = SetView(op="set_view", chart_id=chart.id, kind=found.kind)
    else:
        assert found.kind is not None
        return chart, found.kind, why_not(chart.spec, found.kind, result)
    run = ChartRun(workspace.resolve(action), result, action.op)
    return Planned(Plan(actions=[action]), [run], b"[]")
