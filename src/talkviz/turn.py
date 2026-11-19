"""One turn of a conversation: a question in, charts and a spoken reply out."""

import time
from dataclasses import dataclass

import duckdb

from talkviz import llm
from talkviz.llm import ChartRun, Exchange, Model, Plan
from talkviz.manifest import Manifest
from talkviz.workspace import Workspace


@dataclass
class Turn:
    question: str
    plan: Plan
    runs: list[ChartRun]
    reply: str
    reply_verified: bool
    seconds: dict[str, float]
    """Time spent in each stage: plan (including its queries) and speak."""
    messages: dict[str, bytes]
    """The raw model exchanges for each stage, as JSON."""


def take_turn(
    model: Model,
    question: str,
    manifest: Manifest,
    con: duckdb.DuckDBPyConnection,
    workspace: Workspace,
    history: list[Exchange],
    speaker: Model | None = None,
) -> Turn:
    """Plan and run the charts, write the reply, and update workspace and history.

    `model` plans the charts; `speaker`, if given, writes the reply instead.
    """
    started = time.perf_counter()
    planned = llm.plan(model, question, manifest, con, workspace, history)
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
        seconds={"plan": planned_at - started, "speak": done - planned_at},
        messages={"plan": planned.messages, "speak": spoken.messages},
    )
