"""Score a model on questions with known answers.

An eval file is YAML: a list of {question, sql}, where sql is a query that
answers the question correctly. The model's first chart for the question is
scored by whether its query returns the same data. Each plan is timed.
"""

import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from talkviz import llm
from talkviz.llm import Model
from talkviz.sources import Source
from talkviz.sql import QueryResult, run
from talkviz.workspace import Workspace


@dataclass
class Outcome:
    question: str
    correct: bool
    sql: str | None
    """The model's query, or None if it made no chart."""
    detail: str
    seconds: float
    """Time spent planning, including retries and running the queries."""
    calls: int | None
    """Model requests the plan took (more than one means it was sent back), or
    None if the plan failed."""


def evaluate(model: Model, source: Source, eval_file: Path) -> list[Outcome]:
    cases = yaml.safe_load(eval_file.read_text())
    outcomes = []
    with source.connect() as con:
        for case in cases:
            question = case["question"]
            expected = run(con, case["sql"])
            started = time.perf_counter()
            try:
                planned = llm.plan(
                    model, question, source.manifest, con, Workspace(), []
                )
            except llm.ModelFailed as error:
                seconds = time.perf_counter() - started
                outcomes.append(
                    Outcome(question, False, None, str(error), seconds, None)
                )
                continue
            seconds = time.perf_counter() - started
            calls = _responses(planned.messages)
            if not planned.runs:
                detail = f"no chart: {planned.plan.note}"
                outcomes.append(Outcome(question, False, None, detail, seconds, calls))
                continue
            got = planned.runs[0]
            correct = same_data(expected, got.result)
            detail = "" if correct else "result differs from the expected data"
            outcomes.append(
                Outcome(question, correct, got.chart.sql, detail, seconds, calls)
            )
    return outcomes


def plan_seconds(outcomes: list[Outcome]) -> tuple[float, float]:
    """The median and 90th percentile (nearest rank) of the plan times."""
    seconds = sorted(outcome.seconds for outcome in outcomes)
    if not seconds:
        return 0.0, 0.0
    return statistics.median(seconds), seconds[math.ceil(0.9 * len(seconds)) - 1]


def _responses(messages: bytes) -> int:
    return sum(message.get("kind") == "response" for message in json.loads(messages))


def same_data(expected: QueryResult, got: QueryResult) -> bool:
    """True if every expected column appears in `got` with the same values.

    Column names, column order, row order, and extra columns in `got` are
    ignored; floats are compared to 4 significant figures.
    """
    if len(expected.rows) != len(got.rows):
        return False
    available = [_column(got, c.name) for c in got.columns]
    for column in expected.columns:
        wanted = _column(expected, column.name)
        if wanted not in available:
            return False
        available.remove(wanted)
    return True


def _column(result: QueryResult, name: str) -> list[str]:
    return sorted(_normal(value) for value in result.values(name))


def _normal(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int | float | Decimal):
        return f"{float(value):.4g}"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)
