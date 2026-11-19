"""Chart specs: what the agent may ask the interface to draw.

A spec names columns of a query result; it never contains data. check_spec()
confirms a spec fits the result it will be drawn from, so the interface is
only ever handed charts it can render. views() lists the other ways the same
result could be drawn, which the interface offers as a switch.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from talkviz.sql import QueryResult

MAX_BARS = 60
MAX_SERIES = 8  # the interface has eight series colours
MAX_CELLS = 60  # distinct values along each side of a heatmap


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stat(_Spec):
    """One headline number. The query must return exactly one row."""

    kind: Literal["stat"]
    value: str = Field(description="Numeric column holding the number.")
    label: str = Field(description="Short caption, e.g. 'complaints in 2024'.")


class Table(_Spec):
    """Rows and columns as they are. Use when no chart fits."""

    kind: Literal["table"]


class Bar(_Spec):
    """Compare a number across categories. `series` splits each category:
    side by side for `bar`, stacked into one bar for `stacked_bar`."""

    kind: Literal["bar", "stacked_bar"]
    x: str = Field(description="Column of categories, one bar (or group) each.")
    y: str = Field(description="Numeric column for bar height.")
    series: str | None = Field(None, description="Optional column to split bars by.")


class Line(_Spec):
    """A number over time or another ordered axis. `series` draws several
    lines; `area` fills under the line and stacks the series."""

    kind: Literal["line", "area"]
    x: str = Field(description="Date or numeric column, e.g. a year.")
    y: str = Field(description="Numeric column for line height.")
    series: str | None = Field(None, description="Optional column, one line each.")


class Parts(_Spec):
    """Parts of a whole: `donut` for a few parts, `treemap` for many."""

    kind: Literal["donut", "treemap"]
    label: str = Field(description="Column naming each part.")
    value: str = Field(description="Numeric column for each part's size.")


class Heatmap(_Spec):
    """A number for every pair of two categories, shown as a grid of cells."""

    kind: Literal["heatmap"]
    x: str = Field(description="Column for the grid's columns.")
    y: str = Field(description="Column for the grid's rows.")
    value: str = Field(description="Numeric column for each cell's colour.")


class Scatter(_Spec):
    """How two numbers relate, one point per row. `scatter3d` adds a third."""

    kind: Literal["scatter", "scatter3d"]
    x: str = Field(description="Numeric or date column.")
    y: str = Field(description="Numeric column.")
    z: str | None = Field(None, description="Numeric column; scatter3d only.")
    color: str | None = Field(
        None, description="Optional column to colour points by: categories or a number."
    )
    label: str | None = Field(None, description="Optional column naming each point.")


Spec = Stat | Table | Bar | Line | Parts | Heatmap | Scatter

# What to call each kind when speaking.
KIND_NAMES = {
    "stat": "a single number",
    "table": "a table",
    "bar": "a bar chart",
    "stacked_bar": "a stacked bar chart",
    "line": "a line chart",
    "area": "an area chart",
    "donut": "a donut chart",
    "treemap": "a treemap",
    "heatmap": "a heatmap",
    "scatter": "a scatter plot",
    "scatter3d": "a 3D scatter plot",
}
ChartSpec = Annotated[Spec, Field(discriminator="kind")]

_DISCRETE = ("text", "number", "boolean", "time")
_ORDERED = ("number", "time")


def check_spec(spec: Spec, result: QueryResult, legible: bool = True) -> list[str]:
    """Reasons the spec cannot be drawn from the result; empty if it can.

    Some rules are about correctness (the columns exist and have the right
    types) and always apply. `legible` adds the rules about readability, such
    as how many bars fit. Those steer what the model plans, but never stop the
    user choosing a view: the interface draws what fits and says what it left
    out.
    """
    if not result.rows:
        return ["The query returned no rows, so there is nothing to draw."]
    if isinstance(spec, Table):
        return []
    check = _Check(result)
    if isinstance(spec, Stat):
        check.column("value", spec.value, ("number",))
        if len(result.rows) != 1:
            check.problems.append(
                f"A stat needs a query that returns one row; this returns"
                f" {len(result.rows)}."
            )
        return check.problems
    if result.truncated:
        return ["The query returns too many rows to chart; aggregate or filter it."]
    if isinstance(spec, Bar | Line):
        _check_bars_and_lines(spec, check, legible)
    elif isinstance(spec, Parts):
        _check_parts(spec, check)
    elif isinstance(spec, Heatmap):
        _check_heatmap(spec, check)
    else:
        _check_scatter(spec, check)
    return check.problems


def views(spec: Spec, result: QueryResult) -> list[Spec]:
    """Every way this chart's result can be drawn, starting with `spec` itself.

    The others reuse the same columns in the same roles, so switching between
    them needs no new query. A table is always last.
    """
    found = [spec]
    for kind in _FAMILY:
        other = _convert(spec, kind)
        if other is None or other in found:
            continue
        if not check_spec(other, result, legible=False):
            found.append(other)
    table = Table(kind="table")
    return found if table in found else [*found, table]


def axis(spec: Spec, result: QueryResult) -> tuple[list[str], list[str]]:
    """The chart's distinct x values and series names, in result order, as
    text. Both empty for kinds that cannot be zoomed."""
    if not isinstance(spec, Bar | Line) or check_spec(spec, result, legible=False):
        return [], []
    xs = list(dict.fromkeys(_text(value) for value in result.values(spec.x)))
    if spec.series is None:
        return xs, []
    return xs, list(dict.fromkeys(_text(v) for v in result.values(spec.series)))


def _text(value: object) -> str:
    """A cell as the interface writes it, so both sides agree on axis values."""
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, float | Decimal) and value == int(value):
        return str(int(value))
    return str(value)


def fit_lens(
    spec: Spec,
    result: QueryResult,
    start: str | None,
    end: str | None,
    hidden: list[str],
) -> tuple[str | None, str | None, list[str]]:
    """(start, end, hidden) as they apply to this chart, with names matched
    regardless of case and the range in axis order. Raises ValueError with the
    reasons if they do not apply."""
    xs, series = axis(spec, result)
    if not xs:
        raise ValueError("Only line, area, and bar charts can be zoomed.")
    problems = []

    def match(name: str, known: list[str], what: str) -> str | None:
        found = next((k for k in known if k.lower() == name.strip().lower()), None)
        if found is None:
            shown = ", ".join(known[:40]) or "none"
            problems.append(f"{name!r} is not one of the chart's {what}: {shown}.")
        return found

    if (start is None) != (end is None):
        problems.append("Give both ends of the range to zoom to.")
        start = end = None
    if start is not None and end is not None:
        start, end = match(start, xs, "x values"), match(end, xs, "x values")
        if start is not None and end is not None and xs.index(start) > xs.index(end):
            start, end = end, start
    names = [found for name in hidden if (found := match(name, series, "series"))]
    if series and len(set(names)) >= len(series):
        problems.append("At least one series must stay in view.")
    if problems:
        raise ValueError(" ".join(problems))
    return start, end, names


def why_not(spec: Spec, kind: str, result: QueryResult) -> str:
    """Why this chart's result cannot be drawn as `kind`, for a kind that
    views() does not offer."""
    other = _convert(spec, kind) if kind in _FAMILY else None
    if other is None:
        return f"its columns cannot play the roles a {kind} chart needs."
    return (
        " ".join(check_spec(other, result, legible=False))
        or "it is not a known chart kind."
    )


class _Check:
    def __init__(self, result: QueryResult) -> None:
        self.result = result
        self.problems: list[str] = []

    def column(self, role: str, name: str, kinds: tuple[str, ...]) -> bool:
        """Note a problem unless the named column exists with one of the kinds."""
        column = self.result.column(name)
        if column is None:
            available = ", ".join(f"{c.name} ({c.kind})" for c in self.result.columns)
            self.problems.append(
                f"{role} column {name!r} is not in the query result."
                f" Its columns are: {available}."
            )
        elif column.kind not in kinds:
            self.problems.append(
                f"{role} column {name!r} is {column.kind}; it must be"
                f" {' or '.join(kinds)}."
            )
        else:
            return True
        return False

    def one_row_each(self, *names: str) -> None:
        keys = list(zip(*(self.result.values(name) for name in names), strict=True))
        if len(set(keys)) < len(keys):
            columns = " and ".join(repr(name) for name in names)
            self.problems.append(
                f"The result has several rows for the same {columns};"
                " group the query so there is one row each."
            )

    def not_negative(self, name: str, what: str) -> None:
        if any(value is not None and value < 0 for value in self.result.values(name)):
            self.problems.append(f"{name!r} has negative values, which {what}.")

    def distinct(self, name: str) -> int:
        return len(set(self.result.values(name)))


def _check_bars_and_lines(spec: Bar | Line, check: _Check, legible: bool) -> None:
    ordered = isinstance(spec, Line)
    ok = check.column("x", spec.x, _ORDERED if ordered else _DISCRETE)
    ok = check.column("y", spec.y, ("number",)) and ok
    if spec.series is not None:
        ok = check.column("series", spec.series, _DISCRETE) and ok
    if not ok:
        return
    check.one_row_each(*([spec.x, spec.series] if spec.series else [spec.x]))
    if spec.series and check.distinct(spec.series) > MAX_SERIES:
        check.problems.append(
            f"{spec.series!r} has {check.distinct(spec.series)} values; a chart can"
            f" show at most {MAX_SERIES} series. Keep the top few in the query."
        )
    if legible and not ordered and check.distinct(spec.x) > MAX_BARS:
        check.problems.append(
            f"{check.distinct(spec.x)} bars is too many; keep at most {MAX_BARS}"
            " with ORDER BY and LIMIT."
        )
    if ordered and check.distinct(spec.x) < 2:
        check.problems.append("A line needs at least two x values; use a stat or bar.")
    if spec.kind in ("stacked_bar", "area") and spec.series:
        check.not_negative(spec.y, "cannot be stacked")
    if spec.kind == "stacked_bar" and not spec.series:
        check.problems.append("A stacked bar needs a series column to stack by.")


def _check_parts(spec: Parts, check: _Check) -> None:
    # Named categories only: shares of a year or an amount rarely mean anything.
    ok = check.column("label", spec.label, ("text", "boolean"))
    if not (check.column("value", spec.value, ("number",)) and ok):
        return
    check.one_row_each(spec.label)
    check.not_negative(spec.value, "cannot be parts of a whole")
    parts = check.distinct(spec.label)
    if parts < 2:
        check.problems.append("Parts of a whole need at least two parts; use a stat.")
    # No upper limit: the interface groups the smallest parts as "Other".


def _check_heatmap(spec: Heatmap, check: _Check) -> None:
    ok = check.column("x", spec.x, _DISCRETE)
    ok = check.column("y", spec.y, _DISCRETE) and ok
    if not (check.column("value", spec.value, ("number",)) and ok):
        return
    check.one_row_each(spec.x, spec.y)
    for name in (spec.x, spec.y):
        if check.distinct(name) > MAX_CELLS:
            check.problems.append(
                f"{name!r} has {check.distinct(name)} values; a heatmap can show at"
                f" most {MAX_CELLS} along each side."
            )


def _check_scatter(spec: Scatter, check: _Check) -> None:
    check.column("x", spec.x, _ORDERED)
    check.column("y", spec.y, ("number",))
    if spec.kind == "scatter3d":
        if spec.z is None:
            check.problems.append("A scatter3d needs a z column.")
        else:
            check.column("z", spec.z, ("number",))
    elif spec.z is not None:
        check.problems.append("Only scatter3d takes a z column.")
    if spec.label is not None:
        check.column("label", spec.label, _DISCRETE)
    if spec.color is not None and check.column("color", spec.color, _DISCRETE):
        column = check.result.column(spec.color)
        categories = check.distinct(spec.color)
        if column is not None and column.kind != "number" and categories > MAX_SERIES:
            check.problems.append(
                f"{spec.color!r} has {categories} values; points can be coloured by"
                f" at most {MAX_SERIES} categories."
            )


def _convert(spec: Spec, kind: str) -> Spec | None:
    """A `kind` chart showing what `spec` shows, if its columns fit the roles."""
    fields = _roles(spec, _FAMILY[kind])
    if fields is None:
        return None
    return _ADAPTER.validate_python({"kind": kind, **fields})


def _roles(spec: Spec, target: str) -> dict[str, Any] | None:
    xy = target in ("bar", "line")
    if isinstance(spec, Bar | Line):
        if xy:
            return {"x": spec.x, "y": spec.y, "series": spec.series}
        if target == "parts" and spec.series is None:
            return {"label": spec.x, "value": spec.y}
        if target == "heatmap" and spec.series is not None:
            return {"x": spec.x, "y": spec.series, "value": spec.y}
    if isinstance(spec, Parts):
        if target == "parts":
            return {"label": spec.label, "value": spec.value}
        if target == "bar":
            return {"x": spec.label, "y": spec.value}
    if isinstance(spec, Heatmap) and xy:
        return {"x": spec.x, "y": spec.value, "series": spec.y}
    if isinstance(spec, Scatter) and target == "scatter" and spec.z is not None:
        # Flattened to two dimensions, the third number becomes the colour.
        color = spec.color or spec.z
        return {"x": spec.x, "y": spec.y, "color": color, "label": spec.label}
    return None


_ADAPTER: TypeAdapter[Spec] = TypeAdapter(ChartSpec)
# Kinds views() tries, in the order they are offered, with the spec class
# ("family") whose fields each one takes.
_FAMILY = {
    "line": "line",
    "area": "line",
    "bar": "bar",
    "stacked_bar": "bar",
    "donut": "parts",
    "treemap": "parts",
    "heatmap": "heatmap",
    "scatter": "scatter",
}
