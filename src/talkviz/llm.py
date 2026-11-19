"""The two model calls in a turn: plan the charts, then write the reply.

This is the only module that imports pydantic_ai. Both calls validate what the
model returns and send it back with the reasons when it fails:

- plan():  every query is linted, type-checked, and run; every chart spec is
           checked against its query's result
- speak(): every number in the reply must come from those results
"""

import os
from dataclasses import dataclass, field
from typing import Literal, cast, get_args

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import duckdb
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import (
    Agent,
    AgentRunError,
    ModelRetry,
    PromptedOutput,
    RunContext,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from talkviz.charts import check_spec
from talkviz.manifest import Manifest
from talkviz.speech import numbers_in, unsupported_numbers
from talkviz.sql import QueryRejected, QueryResult, check_and_run
from talkviz.workspace import Action, Chart, CreateChart, Workspace

DEFAULT_MODEL = "anthropic/claude-opus-5.5"
# None leaves reasoning at the model's own default.
DEFAULT_EFFORT: "Effort | None" = None
MAX_ACTIONS = 3
RETRIES = 2
# Rows of each result shown to the model when it writes the reply.
SPEECH_ROWS = 40


Effort = Literal["max", "xhigh", "high", "medium", "low", "minimal", "none"]
EFFORTS: tuple[str, ...] = get_args(Effort)


def openrouter(name: str | None = None, effort: str | None = None) -> Model:
    """An OpenRouter model; TALKVIZ_MODEL or DEFAULT_MODEL if no name is given.

    `effort` sets how much the model reasons before answering: one of EFFORTS,
    else TALKVIZ_EFFORT, else DEFAULT_EFFORT. Needs OPENROUTER_API_KEY in the
    environment.
    """
    name = name or os.environ.get("TALKVIZ_MODEL") or DEFAULT_MODEL
    effort = effort or os.environ.get("TALKVIZ_EFFORT") or DEFAULT_EFFORT
    settings = None
    if effort:
        if effort not in EFFORTS:
            raise ValueError(
                f"unknown reasoning effort {effort!r}; use one of {', '.join(EFFORTS)}"
            )
        settings = OpenRouterModelSettings(
            openrouter_reasoning={"effort": cast(Effort, effort)}
        )
    return OpenRouterModel(name, provider=OpenRouterProvider(), settings=settings)


class ModelFailed(Exception):
    """The model call failed, or it never returned a plan that passed validation."""


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[Action] = Field(
        description="Charts to create, update, or show. Empty if none apply."
    )
    note: str | None = Field(
        None,
        description=(
            "Only when there are no actions: why not, or the clarifying question"
            " to ask."
        ),
    )


@dataclass
class ChartRun:
    """A chart the plan touched, with the result of running its query."""

    chart: Chart
    result: QueryResult
    op: str


@dataclass
class Exchange:
    question: str
    reply: str


@dataclass
class PlanDeps:
    manifest: Manifest
    con: duckdb.DuckDBPyConnection
    workspace: Workspace
    runs: list[ChartRun] = field(default_factory=list)


@dataclass
class Planned:
    plan: Plan
    runs: list[ChartRun]
    messages: bytes
    """The raw model exchange as JSON, for logging."""


@dataclass
class Spoken:
    text: str
    verified: bool
    """False if the model never produced a reply whose numbers checked out, and
    `text` is a fallback."""
    messages: bytes


def plan(
    model: Model,
    question: str,
    manifest: Manifest,
    con: duckdb.DuckDBPyConnection,
    workspace: Workspace,
    history: list[Exchange],
) -> Planned:
    """Ask the model for chart actions; all returned actions have been run."""
    deps = PlanDeps(manifest, con, workspace)
    agent = Agent(
        model,
        output_type=PromptedOutput(Plan),
        deps_type=PlanDeps,
        instructions=_plan_instructions(manifest),
        retries=RETRIES,
    )
    agent.output_validator(_validate_plan)
    try:
        result = agent.run_sync(_plan_prompt(question, workspace, history), deps=deps)
    except AgentRunError as error:
        raise ModelFailed(str(error)) from error
    return Planned(result.output, deps.runs, result.all_messages_json())


def speak(
    model: Model,
    question: str,
    manifest: Manifest,
    planned: Planned,
    history: list[Exchange],
) -> Spoken:
    """Ask the model for the spoken reply to a planned turn."""
    allowed = numbers_in([question])
    for run in planned.runs:
        allowed |= numbers_in([run.chart.title, run.chart.sql, len(run.result.rows)])
        allowed |= numbers_in(value for row in run.result.rows for value in row)
    agent = Agent(
        model,
        deps_type=set,
        instructions=_speech_instructions(manifest),
        retries=RETRIES,
    )
    agent.output_validator(_validate_speech)
    prompt = _speech_prompt(question, planned, history)
    try:
        result = agent.run_sync(prompt, deps=allowed)
    except UnexpectedModelBehavior:  # retries ran out; other errors propagate
        titles = " and ".join(run.chart.title for run in planned.runs)
        fallback = f"Here is {titles}." if titles else "Sorry, I couldn't answer that."
        return Spoken(fallback, verified=False, messages=b"[]")
    return Spoken(
        result.output.strip(), verified=True, messages=result.all_messages_json()
    )


def _validate_plan(ctx: RunContext[PlanDeps], output: Plan) -> Plan:
    deps = ctx.deps
    problems = []
    if not output.actions and not output.note:
        problems.append("Give at least one action, or a note saying why there is none.")
    if len(output.actions) > MAX_ACTIONS:
        problems.append(f"At most {MAX_ACTIONS} actions per turn.")
    runs = []
    for number, action in enumerate(output.actions[:MAX_ACTIONS], start=1):
        where = f"Action {number} ({action.op})"
        try:
            chart = deps.workspace.resolve(action)
        except KeyError as missing:
            known = ", ".join(c.id for c in deps.workspace.charts) or "none yet"
            problems.append(f"{where}: no chart {missing.args[0]!r}. Charts: {known}.")
            continue
        if isinstance(action, CreateChart):
            # Charts created earlier in this plan have taken the next ids.
            chart = chart.model_copy(
                update={"id": f"c{len(deps.workspace.charts) + len(runs) + 1}"}
            )
        others = {c.title.lower() for c in deps.workspace.charts if c.id != chart.id}
        others |= {r.chart.title.lower() for r in runs if r.chart.id != chart.id}
        if isinstance(action, CreateChart) and chart.title.lower() in others:
            problems.append(
                f"{where}: another chart is already titled {chart.title!r}."
            )
        try:
            result = check_and_run(deps.con, chart.sql, deps.manifest)
        except QueryRejected as rejected:
            problems.append(f"{where}: query rejected: {rejected}")
            continue
        problems.extend(f"{where}: {p}" for p in check_spec(chart.spec, result))
        runs.append(ChartRun(chart, result, action.op))
    if problems:
        raise ModelRetry("Fix these problems and answer again:\n" + "\n".join(problems))
    deps.runs = runs
    return output


def _validate_speech(ctx: RunContext[set], output: str) -> str:
    unsupported = unsupported_numbers(output, ctx.deps)
    if unsupported:
        raise ModelRetry(
            "These numbers are not in the query results: "
            + ", ".join(unsupported)
            + ". Say only figures that appear in the results, and do not"
            " compute new ones."
        )
    return output


def _plan_instructions(manifest: Manifest) -> str:
    tables = []
    for table in manifest.tables:
        lines = [f"### {table.name}\n{table.description.strip()}"]
        for column in table.columns:
            line = f"- {column.name} ({column.type}): {column.description.strip()}"
            if column.values:
                line += f" Values: {', '.join(column.values)}."
            lines.append(line)
        tables.append("\n".join(lines))
    examples = "\n\n".join(
        f"Question: {e.question}\n```sql\n{e.sql.strip()}\n```"
        for e in manifest.examples
    )
    return f"""\
You help someone explore a dataset by voice. Each turn they say something, and
you decide which charts to create, change, or bring back. A separate step
writes the spoken reply from your charts' results, so you only plan charts.

# Dataset: {manifest.title}

{manifest.description.strip()}

## Tables

{chr(10).join(tables)}

## Caveats

{_bullets(manifest.caveats)}

## Example queries

{examples}

# Queries

- Write one DuckDB SELECT statement per chart, reading only the tables above.
- Return the rows the chart draws and nothing more: aggregate in the query,
  one row per bar or point, ordered the way it should read.
- Name result columns in plain snake_case words; they label the axes.
- If the reply should mention a share, change, or total, compute it in the
  query. The spoken reply can only use numbers that appear in the result.

# Charts

- stat: one headline number, from a query returning one row.
- line: a number over time or another ordered axis.
- bar: a number compared across categories.
- table: a listing of records, or anything the others do not fit.
- `series` splits a bar or line by a second column; keep to a few values.

# Actions

- create_chart for a new question.
- update_chart when they ask to change a chart they already have ("make that
  a bar chart", "only since 2015"). Send only the fields that change.
- show_chart when they ask to see an existing chart again.
- Titles are how the user refers to charts aloud: short, specific, distinct.
- If the question cannot be answered from this dataset, or is too vague to
  chart, return no actions and a note that says why or asks what they mean.
"""


def _speech_instructions(manifest: Manifest) -> str:
    return f"""\
You are the voice of a data exploration tool for: {manifest.title}. The user
asked something aloud and charts were made in response. Write what to say
back. It will be read out by a text-to-speech voice, and the user can see the
charts.

- One to three short sentences of plain spoken English. No lists, symbols,
  formatting, or column names.
- Lead with the answer, then the most notable thing in the data.
- Use only numbers that appear in the results below, written in digits. You
  may round them ("about 12,400"). Do not calculate new figures.
- Mention a caveat only when it changes how this answer should be read:
{_bullets(manifest.caveats)}
- If no chart was made, say why in one sentence, or ask the clarifying
  question you were given.
"""


def _plan_prompt(question: str, workspace: Workspace, history: list[Exchange]) -> str:
    parts = []
    if history:
        parts.append("Conversation so far:\n" + _transcript(history))
    if workspace.charts:
        charts = "\n".join(
            f"- {c.id}: {c.title!r} spec={c.spec.model_dump_json(exclude_none=True)}"
            f"\n  sql: {' '.join(c.sql.split())}"
            for c in workspace.charts
        )
        parts.append("Charts in this chat (the last is the most recent):\n" + charts)
    parts.append(f"The user says: {question}")
    return "\n\n".join(parts)


def _speech_prompt(question: str, planned: Planned, history: list[Exchange]) -> str:
    parts = []
    if history:
        parts.append("Conversation so far:\n" + _transcript(history))
    parts.append(f"The user says: {question}")
    if not planned.runs:
        parts.append(f"No chart was made. Reason: {planned.plan.note}")
    for run in planned.runs:
        result = run.result
        header = " | ".join(c.name for c in result.columns)
        rows = "\n".join(
            " | ".join(str(value) for value in row) for row in result.rows[:SPEECH_ROWS]
        )
        shown = (
            f"first {SPEECH_ROWS} of {len(result.rows)} rows"
            if len(result.rows) > SPEECH_ROWS
            else f"{len(result.rows)} rows"
        )
        parts.append(
            f"Chart {run.op.removesuffix('_chart')}d: {run.chart.title!r}"
            f" ({run.chart.spec.kind}, {shown})\n{header}\n{rows}"
        )
    return "\n\n".join(parts)


def _transcript(history: list[Exchange]) -> str:
    return "\n".join(f"User: {e.question}\nYou: {e.reply}" for e in history[-6:])


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {' '.join(item.split())}" for item in items) or "- None."
