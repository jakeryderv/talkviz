"""The two model calls in a turn: plan the charts, then write the reply.

This is the only module that imports pydantic_ai. Both calls validate what the
model returns and send it back with the reasons when it fails:

- plan():  every query is linted, type-checked, and run; every chart spec is
           checked against its query's result
- speak(): every number in the reply must come from those results

speak_stream() is speak() for a reply that is shown or read out as it is
written; it checks each sentence before passing it on.
"""

import os
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast, get_args

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import duckdb
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import (
    Agent,
    AgentRunError,
    ModelAPIError,
    ModelRetry,
    PromptedOutput,
    RunContext,
    UnexpectedModelBehavior,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider

from talkviz.charts import KIND_NAMES, Bar, Line, check_spec, fit_lens, views, why_not
from talkviz.manifest import Manifest
from talkviz.speech import numbers_in, unsupported_numbers
from talkviz.sql import QueryRejected, QueryResult
from talkviz.workspace import (
    Action,
    Chart,
    CreateChart,
    Lens,
    NarrowChart,
    NoPast,
    NoRoom,
    SetLens,
    SetView,
    UpdateChart,
    Workspace,
    run_chart,
)

Effort = Literal["max", "xhigh", "high", "medium", "low", "minimal", "none"]
EFFORTS: tuple[str, ...] = get_args(Effort)

# Chosen for speed at equal accuracy on the eval: about 1.3 s to plan and 1 s
# to reply, against about 10 s a turn with one larger model doing both.
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
# None leaves reasoning at the model's own default.
DEFAULT_EFFORT: Effort | None = "low"
# The model that writes the reply; None means the planning model does.
DEFAULT_SPEECH_MODEL: str | None = "anthropic/claude-haiku-5.5"
DEFAULT_SPEECH_EFFORT: Effort | None = "none"
MAX_ACTIONS = 4
RETRIES = 2
# Rows of each result shown to the model when it writes the reply.
SPEECH_ROWS = 200


def openrouter(name: str | None = None, effort: str | None = None) -> Model:
    """An OpenRouter model; TALKVIZ_MODEL or DEFAULT_MODEL if no name is given.

    `effort` sets how much the model reasons before answering: one of EFFORTS,
    else TALKVIZ_EFFORT, else DEFAULT_EFFORT. Needs OPENROUTER_API_KEY in the
    environment.
    """
    chosen = name or os.environ.get("TALKVIZ_MODEL")
    effort = effort or os.environ.get("TALKVIZ_EFFORT")
    if not chosen:
        # The default effort was tuned for the default model, so it applies
        # only to that; another model keeps its own default.
        return _openrouter(DEFAULT_MODEL, effort or DEFAULT_EFFORT)
    return _openrouter(chosen, effort)


def speech_openrouter(
    name: str | None = None, effort: str | None = None
) -> Model | None:
    """The OpenRouter model that writes replies, or None if the planner does.

    Like openrouter(), with TALKVIZ_SPEECH_MODEL, TALKVIZ_SPEECH_EFFORT,
    DEFAULT_SPEECH_MODEL and DEFAULT_SPEECH_EFFORT.
    """
    chosen = name or os.environ.get("TALKVIZ_SPEECH_MODEL")
    effort = effort or os.environ.get("TALKVIZ_SPEECH_EFFORT")
    if chosen:
        return _openrouter(chosen, effort)
    if not DEFAULT_SPEECH_MODEL:
        return None
    return _openrouter(DEFAULT_SPEECH_MODEL, effort or DEFAULT_SPEECH_EFFORT)


def exact(name: str, effort: str | None = None) -> Model:
    """The OpenRouter model `name` with exactly this effort: no defaults and
    nothing from the environment but the API key."""
    return _openrouter(name, effort)


MAX_TOKENS = 6000
MODEL_TIMEOUT = 60.0  # seconds, for one model call


def _openrouter(name: str, effort: str | None) -> Model:
    # A plan or a reply is short. The limits stop a model that runs on
    # without end from holding a turn for minutes.
    settings = OpenRouterModelSettings(max_tokens=MAX_TOKENS, timeout=MODEL_TIMEOUT)
    if effort:
        if effort not in EFFORTS:
            raise ValueError(
                f"unknown reasoning effort {effort!r}; use one of {', '.join(EFFORTS)}"
            )
        settings["openrouter_reasoning"] = {"effort": cast(Effort, effort)}
    return OpenRouterModel(name, provider=OpenRouterProvider(), settings=settings)


class ModelFailed(Exception):
    """The model call failed, or it never returned a plan that passed validation."""

    def __init__(
        self,
        message: str,
        problems: list[str] | None = None,
        unavailable: str | None = None,
    ) -> None:
        super().__init__(message)
        self.problems = problems or []
        """What was wrong with the last plan, if it got as far as returning one."""
        self.unavailable = unavailable
        """The model's name, if the provider refused or could not run it."""

    @classmethod
    def refused(cls, error: ModelAPIError) -> "ModelFailed":
        """The provider would not run the model: a wrong name, no credit, down."""
        return cls(str(error), unavailable=error.model_name)

    def reply(self) -> str:
        """What to tell the user."""
        if self.unavailable:
            return (
                f"The model {self.unavailable} could not be used. Check the model"
                " name in this chat's settings, or try another model."
            )
        if not self.problems:
            return "Sorry, I couldn't work that out. Try asking another way."
        return f"Sorry, I couldn't do that. {self.problems[0]}"


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
    note: str = ""
    """Something the reply should know about the chart beyond its rows."""


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
    wanted: dict[str, str] = field(default_factory=dict)
    """Chart id to the kind the plan first asked to draw it as, for requests
    that did not fit. Later attempts may not quietly settle for another kind."""
    problems: list[str] = field(default_factory=list)
    """Why the latest plan was sent back, in words the user could be told."""


@dataclass
class Planned:
    plan: Plan
    runs: list[ChartRun]
    messages: bytes
    """The raw model exchange as JSON, for logging."""


@dataclass
class Rejected:
    """A draft reply that was not used."""

    text: str
    unsupported: list[str]
    """The figures in it that are not in the query results."""


@dataclass
class Spoken:
    text: str
    verified: bool
    """False if the model never produced a reply whose numbers checked out, and
    `text` is a fallback."""
    messages: bytes
    rejected: list[Rejected] = field(default_factory=list)
    """Drafts that failed the number check, oldest first, whether or not a
    later one passed."""


@dataclass
class Pivot:
    """A chart's result laid out as the chart is: one value for each x and
    series. A chart with no series has one, named for its y column."""

    x: str
    y: str
    xs: list
    names: list[str]
    cells: dict[tuple[Any, str], Any]
    ordered: bool
    """The x values run in order, as along a line chart."""

    def values(self, name: str) -> list[tuple[Any, Any]]:
        """The series' numeric values, with the x of each."""
        found = [(x, self.cells.get((x, name))) for x in self.xs]
        return [(x, value) for x, value in found if _numeric(value)]

    def extreme(self, x: Any, pick: Callable) -> str | None:
        """The series with the highest (max) or lowest (min) value at x."""
        known = [
            (self.cells[x, name], name)
            for name in self.names
            if _numeric(self.cells.get((x, name)))
        ]
        return pick(known)[1] if len(known) > 1 else None


def _numeric(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


@dataclass
class SpeechDeps:
    allowed: set[float]
    pivots: list[Pivot] = field(default_factory=list)
    """The series charts being spoken about, to check claims against."""
    caveats: list[str] = field(default_factory=list)
    """The manifest's caveats, to tell when a sentence is one."""
    reminded: bool = False
    """A reminder of the data's limits has been said in this conversation."""
    judging: bool = False
    """The question asks for a judgement, so limits belong in the answer."""
    recent: bool = False
    """The question is about the latest period or a fall."""
    shortened: bool = False
    """The reply has been sent back once for being too long."""
    rejected: list[Rejected] = field(default_factory=list)


def plan(
    model: Model,
    question: str,
    manifest: Manifest,
    con: duckdb.DuckDBPyConnection,
    workspace: Workspace,
    history: list[Exchange],
    misfit: tuple[Chart, str, str] | None = None,
    focus: str | None = None,
) -> Planned:
    """Ask the model for chart actions; all returned actions have been run.

    `misfit` is a view already known not to fit: the chart, the kind asked
    for, and why not. The model is told, which saves it finding out. `focus`
    is the id of the chart on screen, which "it" and "this" mean.
    """
    deps = PlanDeps(manifest, con, workspace)
    hint = None
    if misfit:
        chart, kind, reason = misfit
        deps.wanted[chart.id] = kind
        hint = (
            f"They are asking to draw {chart.id} ({chart.title!r}) as {kind!r}."
            f" Its result cannot be drawn that way: {reason} So set_view will"
            f" not work. Either use update_chart with a {kind!r} spec and a query"
            " changed so that it fits, or, if that would not be what they"
            " asked for, return no actions and a note telling them why."
        )
    agent = Agent(
        model,
        output_type=PromptedOutput(Plan),
        deps_type=PlanDeps,
        instructions=_plan_instructions(manifest),
        retries=RETRIES,
    )
    agent.output_validator(_validate_plan)
    try:
        result = agent.run_sync(
            _plan_prompt(question, workspace, history, hint, focus), deps=deps
        )
    except ModelAPIError as error:
        raise ModelFailed.refused(error) from error
    except AgentRunError as error:
        raise ModelFailed(str(error), deps.problems) from error
    return Planned(result.output, deps.runs, result.all_messages_json())


def speak(
    model: Model,
    question: str,
    manifest: Manifest,
    planned: Planned,
    history: list[Exchange],
    as_of: str | None = None,
) -> Spoken:
    """Ask the model for the spoken reply to a planned turn. `as_of` is the
    data's version, usually the date it runs to, so the reply need not guess."""
    allowed = _allowed_numbers(question, manifest, planned) | numbers_in([as_of or ""])
    reminded = _reminded(history, manifest.caveats)
    judging = _judging(question)
    deps = SpeechDeps(
        allowed,
        _pivots(planned),
        manifest.caveats,
        reminded,
        judging=judging,
        recent=bool(_RECENT.search(question)),
    )
    agent = Agent(
        model,
        deps_type=SpeechDeps,
        instructions=_speech_instructions(manifest, _judging(question)),
        retries=RETRIES,
    )
    agent.output_validator(_validate_speech)
    prompt = _speech_prompt(question, planned, history, as_of)
    try:
        result = agent.run_sync(prompt, deps=deps)
    except UnexpectedModelBehavior:  # retries ran out
        return Spoken(_fallback(planned), False, b"[]", deps.rejected)
    except ModelAPIError as error:
        raise ModelFailed.refused(error) from error
    return Spoken(
        result.output.strip(),
        True,
        result.all_messages_json(),
        deps.rejected,
    )


def speak_stream(
    model: Model,
    question: str,
    manifest: Manifest,
    planned: Planned,
    history: list[Exchange],
) -> Iterator[str | Spoken]:
    """speak(), yielding the reply a sentence at a time and then its Spoken.

    Each sentence's numbers are checked before it is yielded, so nothing passed
    on contains a number that is not in the results. If the first sentence fails,
    the model tries again as in speak(). If a later one fails, what was said
    cannot be taken back, so the reply ends at the last good sentence and is
    marked unverified. Either way the Spoken's text is the yielded chunks joined.
    """
    allowed = _allowed_numbers(question, manifest, planned)
    agent = Agent(
        model,
        instructions=_speech_instructions(manifest, _judging(question)),
    )
    first = prompt = _speech_prompt(question, planned, history)
    said: list[str] = []
    rejected: list[Rejected] = []
    for _ in range(RETRIES + 1):
        with agent.run_stream_sync(prompt) as stream:
            deltas = stream.stream_text(delta=True, debounce_by=None)
            kept = _sentences(deltas)
            if not _judging(question):
                reminded = _reminded(history, manifest.caveats)
                recent = bool(_RECENT.search(question))
                kept = _unreminded(kept, manifest.caveats, reminded, recent)
            for sentence in kept:
                unsupported = unsupported_numbers(sentence, allowed)
                if unsupported:
                    rejected.append(Rejected(sentence, unsupported))
                    break
                yield f" {sentence}" if said else sentence
                said.append(sentence)
            else:
                messages = stream.all_messages_json()
                yield Spoken(" ".join(said), True, messages, rejected)
                return
            messages = stream.all_messages_json()
        if said:
            yield Spoken(" ".join(said), False, messages, rejected)
            return
        prompt = f"{first}\n\n{_unsupported(unsupported)}"
    yield _fallback(planned)
    yield Spoken(_fallback(planned), False, b"[]", rejected)


def _validate_plan(ctx: RunContext[PlanDeps], output: Plan) -> Plan:
    deps = ctx.deps
    problems, told = [], []
    if not output.actions and not output.note:
        problems.append("Give at least one action, or a note saying why there is none.")
    if len(output.actions) > MAX_ACTIONS:
        problems.append(f"At most {MAX_ACTIONS} actions per turn.")
    runs = []
    for number, action in enumerate(output.actions[:MAX_ACTIONS], start=1):
        where = f"Action {number} ({action.op})"
        # Charts made earlier in this plan have taken the next ids.
        made = [run.chart for run in runs if run.op in ("create_chart", "narrow_chart")]
        ahead = Workspace(
            charts=[*deps.workspace.charts, *made], past=deps.workspace.past
        )
        try:
            chart = ahead.resolve(action)
        except KeyError as missing:
            known = ", ".join(c.id for c in deps.workspace.charts) or "none yet"
            problems.append(f"{where}: no chart {missing.args[0]!r}. Charts: {known}.")
            continue
        except NoPast as none:
            told.append(str(none))
            problems.append(
                f"{where}: {none} Return no actions and a note telling the user that."
            )
            continue
        except NoRoom as full:
            told.append(str(full))
            problems.append(
                f"{where}: {full} Return no actions and a note telling the user"
                " that, and offering to make it a new chart of its own instead."
            )
            continue
        others = {c.title.lower() for c in deps.workspace.charts if c.id != chart.id}
        others |= {r.chart.title.lower() for r in runs if r.chart.id != chart.id}
        if (
            isinstance(action, CreateChart | NarrowChart)
            and chart.title.lower() in others
        ):
            problems.append(
                f"{where}: another chart is already titled {chart.title!r}."
            )
        try:
            result = run_chart(deps.con, deps.manifest, ahead, chart)
        except QueryRejected as rejected:
            problems.append(f"{where}: query rejected: {rejected}")
            continue
        except ValueError as wrong:
            problems.append(
                f"{where}: {wrong} narrow_chart can only keep rows the chart's"
                " result already has. If the user wants rows it does not have,"
                " use create_chart with `parent` set to this chart and a new query."
            )
            continue
        problems.extend(f"{where}: {p}" for p in check_spec(chart.spec, result))
        op = action.op
        current = deps.workspace.get(chart.id)
        asked = deps.wanted.get(chart.id)
        if asked and chart.shown != asked:
            problems.append(
                f"{where}: you first asked to draw {chart.title!r} as {asked!r}"
                f" and are now drawing it as {chart.shown!r}. Draw it as"
                f" {asked!r}, or return no actions and a note saying why not."
            )
        if isinstance(action, SetLens) and chart.lens is not None:
            lens = chart.lens
            try:
                fitted = fit_lens(chart.spec, result, lens.start, lens.end, lens.hidden)
            except ValueError as wrong:
                problems.append(
                    f"{where}: {wrong} If the user wants different data, not a"
                    " different part of this chart, use update_chart."
                )
            else:
                start, end, hidden = fitted
                moved = Lens(start=start, end=end, hidden=hidden)
                chart = chart.model_copy(update={"lens": moved})
        if isinstance(action, SetView):
            fits = [view.kind for view in views(chart.spec, result)]
            if action.kind not in fits:
                deps.wanted[chart.id] = action.kind
                name = KIND_NAMES.get(action.kind, action.kind)
                reason = why_not(chart.spec, action.kind, result)
                told.append(f"{chart.title} can't be drawn as {name}: {reason}")
                # Say what is wrong, not what else would fit: offered the
                # alternatives, the model picks one the user did not ask for.
                problems.append(
                    f"{where}: the result of {chart.title!r} cannot be drawn as"
                    f" {action.kind!r}: {reason}"
                    f" The user asked for {action.kind!r}, so do not draw it as"
                    " another kind. Use update_chart on this chart instead, with"
                    f" a {action.kind!r} spec and a query changed so that it"
                    " fits. If no query could, return no actions and a note"
                    " telling the user why it cannot be done."
                )
        elif (
            isinstance(action, UpdateChart)
            and current is not None
            and (chart.sql, chart.title) == (current.sql, current.title)
            and chart.spec in views(current.spec, result)
        ):
            # Only how the same result is drawn changed: a view, not a revision.
            view = None if chart.spec.kind == current.spec.kind else chart.spec.kind
            chart, op = current.model_copy(update={"view": view}), "set_view"
        note = ""
        redefined = op in ("update_chart", "revert_chart")
        if redefined and current is not None and current.lens is not None:
            note = "The chart is back to its full view: no zoom, no series hidden."
        runs.append(ChartRun(chart, result, op, note))
    deps.problems = told
    if problems:
        raise ModelRetry("Fix these problems and answer again:\n" + "\n".join(problems))
    deps.runs = runs
    return output


def _validate_speech(ctx: RunContext[SpeechDeps], output: str) -> str:
    if not ctx.deps.judging:
        kept = _unreminded(
            _split(output), ctx.deps.caveats, ctx.deps.reminded, ctx.deps.recent
        )
        output = " ".join(kept)
    unsupported = unsupported_numbers(output, ctx.deps.allowed)
    if unsupported:
        ctx.deps.rejected.append(Rejected(output.strip(), unsupported))
        raise ModelRetry(_unsupported(unsupported))
    if _SECOND_THOUGHTS.search(output):
        raise ModelRetry(
            "The reply corrects itself partway. Write it again, giving only the"
            " final answer."
        )
    if len(output.split()) > LONGEST_REPLY and not ctx.deps.shortened:
        ctx.deps.shortened = True
        raise ModelRetry(
            "That is too long to listen to. Say it again in under 40 words:"
            " the answer, and at most one thing that stands out."
        )
    overclaims = _overclaims(output, ctx.deps.pivots)
    if overclaims:
        raise ModelRetry(
            "The reply says something holds throughout, and the results show"
            " it does not:\n"
            + "\n".join(overclaims)
            + "\nWrite it again, saying where it holds or leaving the claim out."
        )
    return output


# A question that asks for a judgement the data's limits bear on, where a
# limit belongs in the answer.
_JUDGING = re.compile(
    r"\b(?:safe|safer|safest|unsafe|danger|reliab|best|worst|better|worse|trust"
    r"|accurate|really|actually|fair|why|should|blame|fault|defect|mean)",
    re.IGNORECASE,
)
LONGEST_REPLY = 60  # words, before the reply is asked for again, shorter


# How a sentence that only reminds the user of the data's limits tends to open.
_REMINDER = re.compile(
    r"^(?:but |and |also,? |just |please )?(?:keep in mind|bear in mind|remember"
    r"|note that|do note|that said|a caveat|one caveat|as a reminder|be aware)",
    re.IGNORECASE,
)
_PARTIAL = re.compile(
    r"\b(?:partial|incomplete|only part|partway|part[- ]year|not a full year)",
    re.IGNORECASE,
)


def _judging(question: str) -> bool:
    return bool(_JUDGING.search(question))


def _split(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part]


def _words(text: str) -> set[str]:
    """The longer words of a text, for telling what it is about."""
    return {word for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 4}


def _is_reminder(sentence: str, caveats: list[str]) -> bool:
    """Whether a sentence is there to remind the user of a limit of the data
    rather than to say something about the results. It opens like a reminder;
    or it gives no figures and is mostly a caveat's words, or is about a
    period being incomplete."""
    if _REMINDER.search(sentence):
        return True
    if re.search(r"\d", re.sub(r"\b(?:19|20)\d\d\b", "", sentence)):
        return False  # it reports a figure, so it is about the results
    if _PARTIAL.search(sentence):
        return True
    words = _words(sentence)
    shared = max((len(words & _words(caveat)) for caveat in caveats), default=0)
    return shared >= 3 and shared >= 0.4 * len(words)


def _reminded(history: list[Exchange], caveats: list[str]) -> bool:
    """Whether an earlier reply in the conversation already gave a reminder."""
    return any(
        _is_reminder(sentence, caveats)
        for exchange in history
        for sentence in _split(exchange.reply)
    )


def _unreminded(
    sentences: Iterable[str], caveats: list[str], reminded: bool, recent: bool = False
) -> Iterator[str]:
    """The sentences of a reply without its repeated reminders. A reply's
    first sentence is its answer and always stays, even when the answer is a
    limit of the data. Of the rest, one reminder is let through in a
    conversation; any after that is dropped, since the limits are on screen."""
    for index, sentence in enumerate(sentences):
        # Asked about the latest period, that it is incomplete is the answer.
        asked = recent and _PARTIAL.search(sentence)
        if index and not asked and _is_reminder(sentence, caveats):
            if reminded:
                continue
            reminded = True
        yield sentence


# A reply that changes its mind aloud: "Ford led, wait, that's wrong, Kia did".
_SECOND_THOUGHTS = re.compile(
    r"\bwait\b[,.:]|\bthat(?:'s| is| was) (?:wrong|not right|incorrect)\b"
    r"|\blet me correct\b|\bcorrection:",
    re.IGNORECASE,
)


def _unsupported(numbers: list[str]) -> str:
    return (
        "These numbers are not in the query results: "
        + ", ".join(numbers)
        + ". Say only figures that appear in the results, and do not"
        " compute new ones."
    )


def _allowed_numbers(question: str, manifest: Manifest, planned: Planned) -> set[float]:
    # The reply is told to mention caveats, so their numbers ("since May 2021")
    # are allowed too.
    allowed = numbers_in([question, *manifest.caveats])
    for run in planned.runs:
        allowed |= numbers_in([run.chart.title, run.chart.sql, len(run.result.rows)])
        allowed |= numbers_in(value for row in run.result.rows for value in row)
    return allowed


def _fallback(planned: Planned) -> str:
    titles = " and ".join(run.chart.title for run in planned.runs)
    return f"Here is {titles}." if titles else "Sorry, I couldn't answer that."


# Where a sentence ends: after . ! or ?, any closing quotes, then whitespace.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


def _sentences(deltas: Iterable[str]) -> Iterator[str]:
    """Regroup streamed text into whole sentences."""
    buffer = ""
    for delta in deltas:
        buffer += delta
        while end := _SENTENCE_END.search(buffer):
            sentence, buffer = buffer[: end.end()].strip(), buffer[end.end() :]
            if sentence:
                yield sentence
    if buffer.strip():
        yield buffer.strip()


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

Pick the kind that answers the question most plainly. The user can switch a
chart between the other kinds its result fits, so do not make a second chart
only to show the same result another way.

- stat: one headline number, from a query returning one row.
- line: a number over time or another ordered axis. area: the same, filled,
  with `series` stacked to show a total and its make-up.
- bar: a number compared across categories. stacked_bar: each bar split by
  `series`, when the total matters as well as the parts.
- donut: shares of a whole. treemap: the same when there are many parts. Use
  these only when the question is about shares; otherwise bar. The smallest
  parts are grouped as "Other" when there are too many to tell apart.
- heatmap: a number for every pair of two categories.
- scatter: how two numbers relate, one point per row; `color` may be a
  category or a third number. scatter3d: three numbers, only when asked for 3D.
- table: a listing of records, or anything the others do not fit.
- `series` splits a bar or line by a second column; keep to a few values.

# Actions

- create_chart for a new question.
- narrow_chart when they ask to filter or cut down a chart they already have
  ("only since 2022", "just Ford and Kia", "the top five"). It makes a
  sub-chart that keeps some rows of that chart's result: `where` picks rows
  by a column's `values` or its `min` and `max`, and `top` with `by` keeps
  the largest few. It cannot total or regroup rows. The original stays.
- create_chart with `parent` when the filter needs rows that chart's result
  does not have ("only brake complaints" on a chart without a component
  column): write the full query, and it too becomes a sub-chart.
- Filtering a sub-chart further ("only since 2022" while looking at 1a) is
  also narrow_chart, on that sub-chart, giving only the new filter. It
  makes a sibling (1b) from the parent's rows with both filters applied.
- A sub-chart with its own query cannot be filtered into another sub-chart,
  and a chart can have 26. You are told when there is no room; then offer a
  new chart of its own.
- update_chart only when they ask to change the chart itself ("change this
  one to...", "replace it", "rename it", "split it by make"). Send only the
  fields that change.
- set_view when they only want an existing chart drawn another way ("make
  that a donut", "show it as a table"). It reuses the chart's result, so it
  works only for kinds that result fits; you are told if it does not. Never
  give a different kind from the one they asked for.
- When something they ask for cannot be done, say so and why in the note. Do
  not do something else in its place without saying.
- set_lens when they only want to move around an existing line, area, or bar
  chart: zoom to a range of its x axis (`start` and `end`, as x values the
  chart has), `zoom_out` to the whole axis, hide or bring back series, or
  `reset` everything. The data is unchanged. Use this for "zoom in on 2020
  to 2024", "hide Ford", "zoom out", "show everything again". Use
  update_chart when they want the data itself narrowed ("only since 2020").
- revert_chart when they ask to undo a change to a chart or put it back how
  it was ("undo that", "go back", "I liked it better before"). It restores
  the chart's previous definition; give `revision` only if they name one.
  Do not rewrite the old query yourself with update_chart.
- discuss when they ask a question that an existing chart's result already
  answers, and want it answered in words: "what does this reveal", "what
  stands out", "which make is highest in 2020", "summarise that". Give one
  for each chart the question is about. Nothing changes; the reply is
  written from those charts' rows. If answering needs data no chart has,
  make a chart instead.
- show_chart when they ask to see an existing chart again. To show several
  charts side by side, give one show_chart for each; the charts of a turn
  are shown together.
- Titles are how the user refers to charts aloud: short, specific, distinct.
- If the question cannot be answered from this dataset, or is too vague to
  chart, return no actions and a note that says why or asks what they mean.
"""


def _limits_rule(manifest: Manifest, judging: bool) -> str:
    """What the reply model is told about the data's limits. They are shown
    beside every chart, so the model is only given them, to answer with, when
    the question asks for a judgement they bear on. Otherwise it is not shown
    them at all: what it is not shown it does not recite."""
    if not judging or not manifest.caveats:
        return (
            "- The data's limits are shown to the user beside every chart. Do\n"
            '  not warn, remind or add "keep in mind" about what the data can\n'
            "  or cannot show."
        )
    return (
        "- The user is asking for a judgement, and these limits bear on it. Let\n"
        "  the one that matters be part of your answer, plainly and once:\n"
        + _bullets(manifest.caveats)
    )


def _speech_instructions(manifest: Manifest, judging: bool = False) -> str:
    return f"""\
You are the voice of a data exploration tool for: {manifest.title}. You and
the user are looking at the same charts and talking them over. They said
something, and charts were made or looked up in response. Write what to say
back. It is read out by a text-to-speech voice.

How to talk:
- Like a colleague beside them, not a report. Answer what they just asked in
  one or two short spoken sentences; three at most when they ask what a
  chart shows. No lists, symbols, formatting, or column names.
- Say the one or two things that matter for their question, with at most
  three figures. Do not go through every series, or recite each peak, low,
  start and end: the chart is in front of them.
- Carry on from the conversation so far. Do not repeat a figure or an
  observation you have already given, and do not describe again a chart
  they have already heard about. After a filter or a change, say what is
  different now.
- Do not open by naming or describing the chart ("Chart 1 shows", "Here
  is", "Looking at chart 1b", "Chart 1b now covers"); start with what you
  have to say. Name a chart by number
  only when it helps tell charts apart, using the number given here, like
  "chart 1a".

Numbers:
- Use only numbers that appear in the results below, written in digits. You
  may round them ("about 12,400"). Do not calculate new figures.
- Never correct yourself in the reply. For which was highest or lowest at
  some point, read the "highest" and "lowest" columns; for which led over
  what span, and where each series started, ended, peaked and bottomed out,
  read the Facts lines. Do not work these out yourself.
- Say that something holds every year, always or throughout only when the
  Facts show it. Otherwise say where it holds.

Limits of the data:
{_limits_rule(manifest, judging)}
- When the Facts say the latest period is incomplete, mention that only if
  you are talking about that period's value, and in a few words.

Otherwise:
- If no chart was made, give the reason itself in one sentence, without
  saying that no chart was made, then make the offer or ask the question
  you were given.
- The data cannot show why something happened; if they ask why, say what
  the data does show and suggest one thing that could be charted next.
- When the rows listed are only the first of many, speak about those and do
  not say the data is incomplete.
"""


def _plan_prompt(
    question: str,
    workspace: Workspace,
    history: list[Exchange],
    hint: str | None,
    focus: str | None = None,
) -> str:
    parts = []
    if history:
        parts.append("Conversation so far:\n" + _transcript(history))
    if workspace.charts:
        charts = "\n".join(
            f"- {c.id}: {c.title!r} spec={c.spec.model_dump_json(exclude_none=True)}"
            + (f" sub-chart of {c.parent}" if c.parent else "")
            + (
                f" keeping rows {c.narrow.model_dump_json(exclude_none=True)}"
                if c.narrow
                else ""
            )
            + (f" drawn as {c.view}" if c.view else "")
            + (f" in view: {c.lens.model_dump_json()}" if c.lens else "")
            + f"\n  sql: {' '.join(c.sql.split())}"
            for c in workspace.charts
        )
        parts.append("Charts in this chat (the last is the most recent):\n" + charts)
        on_screen = next((c for c in workspace.charts if c.id == focus), None)
        if on_screen is not None:
            parts.append(
                f'On screen now: {on_screen.id}, {on_screen.title!r}. "It",'
                ' "this" and requests that name no chart mean this one.'
            )
    parts.append(f"The user says: {question}")
    if hint:
        parts.append(hint)
    return "\n\n".join(parts)


def _speech_prompt(
    question: str, planned: Planned, history: list[Exchange], as_of: str | None = None
) -> str:
    parts = []
    if history:
        parts.append("Conversation so far:\n" + _transcript(history))
    parts.append(f"The user says: {question}")
    if not planned.runs:
        parts.append(f"No chart was made. Reason: {planned.plan.note}")
    for run in planned.runs:
        result = run.result
        header, lines = _table(run.chart, result)
        # The partial last year is news once; after that only when asked about.
        told = any(_PARTIAL.search(exchange.reply) for exchange in history)
        due = not told or _RECENT.search(question)
        facts = _facts(_pivot(run.chart, result), as_of if due else None)
        rows = "\n".join(lines[:SPEECH_ROWS])
        shown = (
            f"first {SPEECH_ROWS} of {len(lines)} rows"
            if len(lines) > SPEECH_ROWS
            else f"{len(lines)} rows"
        )
        parts.append(
            f"Chart {run.chart.label}, {_DID[run.op]}: {run.chart.title!r}"
            f" ({run.chart.shown}, {shown})\n{header}\n{rows}"
            + ("\nFacts:\n" + "\n".join(facts) if facts else "")
            + (f"\n{run.note}" if run.note else "")
        )
    return "\n\n".join(parts)


def _pivot(chart: Chart, result: QueryResult) -> Pivot | None:
    spec = chart.spec
    if not isinstance(spec, Bar | Line):
        return None
    names = [column.name for column in result.columns]
    x, y, series = (getattr(spec, name, None) for name in ("x", "y", "series"))
    if x not in names or y not in names:
        return None
    at_x, at_y = names.index(x), names.index(y)
    at_s = names.index(series) if series in names else None
    cells = {
        (row[at_x], y if at_s is None else str(row[at_s])): row[at_y]
        for row in result.rows
    }
    if len(cells) != len(result.rows):  # more than one value for an x and series
        return None
    return Pivot(
        x=x,
        y=y,
        xs=list(dict.fromkeys(row[at_x] for row in result.rows)),
        names=list(dict.fromkeys(name for _, name in cells)),
        cells=cells,
        ordered=isinstance(spec, Line),
    )


def _pivots(planned: Planned) -> list[Pivot]:
    found = [_pivot(run.chart, run.result) for run in planned.runs]
    return [pivot for pivot in found if pivot is not None]


def _table(chart: Chart, result: QueryResult) -> tuple[str, list[str]]:
    """The result as a header and text rows for the reply model. A chart with
    series is laid out as the chart is, one row for each x value and one
    column for each series, which makes "which was highest in 2020" a matter
    of reading one row."""
    pivot = _pivot(chart, result)
    if pivot is not None and getattr(chart.spec, "series", None):
        header = f"{pivot.x} | " + " | ".join(f"{n} ({pivot.y})" for n in pivot.names)
        lines = []
        for x in pivot.xs:
            values = [pivot.cells.get((x, name), "") for name in pivot.names]
            ends = f"{pivot.extreme(x, max) or ''} | {pivot.extreme(x, min) or ''}"
            lines.append(" | ".join(str(v) for v in [x, *values]) + f" | {ends}")
        return header + " | highest | lowest", lines
    header = " | ".join(column.name for column in result.columns)
    return header, [" | ".join(str(value) for value in row) for row in result.rows]


FACT_SERIES = 12  # series described one by one, at most


def _facts(pivot: Pivot | None, as_of: str | None = None) -> list[str]:
    """Things about a result that are easy to get wrong by eye, worked out
    here so the model reads them: which series led over what span, and where
    each started, ended, peaked and bottomed out. Every number is one from
    the result."""
    if pivot is None or len(pivot.xs) < 2:
        return []
    facts = []
    if pivot.ordered and (partial := _partial(pivot.xs[-1], as_of)):
        facts.append(partial)
    if pivot.ordered and len(pivot.names) > 1:
        for word, pick in (("Highest", max), ("Lowest", min)):
            spans = _spans([(x, pivot.extreme(x, pick)) for x in pivot.xs])
            if spans:
                facts.append(f"{word} {pivot.y}: {spans}.")
    for name in pivot.names[:FACT_SERIES]:
        values = pivot.values(name)
        if len(values) < 2:
            continue
        (peak_x, peak), (low_x, low) = (
            pick(values, key=lambda pair: pair[1]) for pick in (max, min)
        )
        ends = f"peak {peak} at {peak_x}, low {low} at {low_x}"
        if pivot.ordered:
            (first_x, first), (last_x, last) = values[0], values[-1]
            ends = f"starts {first} at {first_x}, ends {last} at {last_x}, {ends}"
        facts.append(f"{name}: {ends}.")
    return facts


# A question about the latest period or about a fall, where it matters that
# the period is incomplete.
_RECENT = re.compile(
    r"\b(?:20\d\d|latest|recent|this year|last year|so far|drop|dropp|declin|fell"
    r"|fall|down|decreas|trend)",
    re.IGNORECASE,
)


def _partial(last: Any, as_of: str | None) -> str | None:
    """That the last year along a chart is not a whole one, if the data's
    version is a date within it. Said as a fact about that point, so the
    reply need not treat it as a warning about everything."""
    found = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", as_of or "")
    if not found or str(last) != found[1] or found[0].endswith("-12-31"):
        return None
    return (
        f"{last} is incomplete: the data runs to {as_of}, so its values are"
        " lower than a full year's would be."
    )


def _spans(leaders: list[tuple[Any, str | None]]) -> str:
    """ "KIA 2018 to 2021, FORD 2022": who led at each x, with runs joined."""
    spans: list[list] = []
    for x, name in leaders:
        if name is None:
            continue
        if spans and spans[-1][0] == name:
            spans[-1][2] = x
        else:
            spans.append([name, x, x])
    return ", ".join(
        f"{name} {first}" if first == last else f"{name} {first} to {last}"
        for name, first, last in spans
    )


# Words that claim something holds at every point, and words that take it back.
_THROUGHOUT = re.compile(
    r"\b(?:every|each|always|throughout|consistently|in all)\b", re.IGNORECASE
)
_EXCEPT = re.compile(r"\b(?:except|apart from|other than|but)\b", re.IGNORECASE)
_RANK = re.compile(
    r"\b(?:most|highest|leads?|led|leading|top|tops|lowest|fewest|least|bottom)\b",
    re.IGNORECASE,
)


def _overclaims(text: str, pivots: list[Pivot]) -> list[str]:
    """Claims in a reply that something holds throughout when the results show
    it does not: "Ford has the most every year" where Kia leads some years,
    or "Toyota is above Honda throughout" where they cross. Only claims that
    name one or two series are judged; anything else is let through."""
    problems = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _THROUGHOUT.search(sentence) or _EXCEPT.search(sentence):
            continue
        for pivot in pivots:
            named = [
                name
                for name in pivot.names
                if len(name) > 1
                and re.search(rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE)
            ]
            if len(pivot.names) < 2 or len(named) not in (1, 2):
                continue
            if len(named) == 2:
                problem = _crossing(pivot, *named)
            elif _RANK.search(sentence):
                problem = _not_always(pivot, named[0])
            else:
                problem = None
            if problem:
                problems.append(problem)
    return problems


def _crossing(pivot: Pivot, one: str, other: str) -> str | None:
    """Where two series swap order, if they do."""
    first, second = dict(pivot.values(one)), dict(pivot.values(other))
    both = [x for x in pivot.xs if x in first and x in second]
    above = [x for x in both if first[x] > second[x]]
    below = [x for x in both if first[x] < second[x]]
    if not above or not below:
        return None
    return (
        f"{one} is above {other} at {above[0]} ({first[above[0]]} to"
        f" {second[above[0]]}) and below it at {below[0]} ({first[below[0]]} to"
        f" {second[below[0]]})."
    )


def _not_always(pivot: Pivot, name: str) -> str | None:
    """Who is highest and lowest at each x, if `name` is not always either."""
    highest = [pivot.extreme(x, max) for x in pivot.xs]
    lowest = [pivot.extreme(x, min) for x in pivot.xs]
    if all(n == name for n in highest if n) or all(n == name for n in lowest if n):
        return None
    return (
        f"Highest: {_spans(list(zip(pivot.xs, highest, strict=True)))}."
        f" Lowest: {_spans(list(zip(pivot.xs, lowest, strict=True)))}."
    )


_DID = {
    "create_chart": "created",
    "update_chart": "updated",
    "revert_chart": "put back to an earlier version, undoing the last change",
    "show_chart": "shown",
    "set_view": "redrawn",
    "set_lens": "zoomed or series hidden",
    "narrow_chart": "made as a sub-chart keeping some rows of another",
    "discuss": "already on screen, which the user is asking about",
}


def view_reply(runs: list[ChartRun]) -> str | None:
    """A reply for a turn that only redrew charts or brought them back, which
    needs no model: there is nothing new to say about the data. None otherwise."""
    if not runs or any(run.op not in _FIXED for run in runs):
        return None
    if len(runs) > 1 and all(run.op == "show_chart" for run in runs):
        titles = [run.chart.title for run in runs]
        return f"Here are {', '.join(titles[:-1])} and {titles[-1]}, side by side."
    return " ".join(_fixed(run) for run in runs)


_FIXED = ("set_view", "set_lens", "show_chart")


def _fixed(run: ChartRun) -> str:
    chart = run.chart
    if run.op == "show_chart":
        return f"Here's {chart.title} again."
    if run.op == "set_view":
        return f"Here's {chart.title} as {KIND_NAMES.get(chart.shown, chart.shown)}."
    if chart.lens is None:
        return f"{chart.title} is back to its full view."
    lens, parts = chart.lens, []
    if lens.start is not None:
        only = lens.start == lens.end
        parts.append(f"shows {lens.start}" + ("" if only else f" to {lens.end}"))
    if lens.hidden:
        names = lens.hidden
        listed = (
            names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
        )
        parts.append(f"hides {listed}")
    return f"{chart.title} now {' and '.join(parts)}."


def _transcript(history: list[Exchange]) -> str:
    return "\n".join(f"User: {e.question}\nYou: {e.reply}" for e in history[-6:])


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {' '.join(item.split())}" for item in items) or "- None."
