"""Score a model on questions with known answers.

An eval file is YAML: a list of {question, sql}, where sql is a query that
answers the question correctly. The model's first chart for the question is
scored by whether its query returns the same data.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai.models import Model

from talkviz import llm
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


def evaluate(model: Model, source: Source, eval_file: Path) -> list[Outcome]:
    cases = yaml.safe_load(eval_file.read_text())
    outcomes = []
    with source.connect() as con:
        for case in cases:
            expected = run(con, case["sql"])
            try:
                planned = llm.plan(
                    model, case["question"], source.manifest, con, Workspace(), []
                )
            except llm.ModelFailed as error:
                outcomes.append(Outcome(case["question"], False, None, str(error)))
                continue
            if not planned.runs:
                detail = f"no chart: {planned.plan.note}"
                outcomes.append(Outcome(case["question"], False, None, detail))
                continue
            got = planned.runs[0]
            correct = same_data(expected, got.result)
            detail = "" if correct else "result differs from the expected data"
            outcomes.append(Outcome(case["question"], correct, got.chart.sql, detail))
    return outcomes


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
