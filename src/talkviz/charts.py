"""Chart specs: what the agent may ask the interface to draw.

A spec names columns of a query result; it never contains data. check_spec()
confirms a spec fits the result it will be drawn from, so the interface is
only ever handed charts it can render.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from talkviz.sql import QueryResult

MAX_BARS = 60
MAX_SERIES = 8  # the interface has eight series colours


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
    """Compare a number across categories; `series` splits each into groups."""

    kind: Literal["bar"]
    x: str = Field(description="Column of categories, one bar (or group) each.")
    y: str = Field(description="Numeric column for bar height.")
    series: str | None = Field(None, description="Optional column to group bars by.")


class Line(_Spec):
    """A number over time or another ordered axis; `series` draws several lines."""

    kind: Literal["line"]
    x: str = Field(description="Date or numeric column, e.g. a year.")
    y: str = Field(description="Numeric column for line height.")
    series: str | None = Field(None, description="Optional column, one line each.")


ChartSpec = Annotated[Stat | Table | Bar | Line, Field(discriminator="kind")]


def check_spec(spec: Stat | Table | Bar | Line, result: QueryResult) -> list[str]:
    """Reasons the spec cannot be drawn from the result; empty if it can."""
    if not result.rows:
        return ["The query returned no rows, so there is nothing to draw."]
    if isinstance(spec, Table):
        return []
    available = ", ".join(f"{c.name} ({c.kind})" for c in result.columns)
    problems = []

    def need(role: str, name: str, kinds: tuple[str, ...]) -> bool:
        column = result.column(name)
        if column is None:
            problems.append(
                f"{role} column {name!r} is not in the query result."
                f" Its columns are: {available}."
            )
        elif column.kind not in kinds:
            problems.append(
                f"{role} column {name!r} is {column.kind}; it must be"
                f" {' or '.join(kinds)}."
            )
        return not problems

    if isinstance(spec, Stat):
        need("value", spec.value, ("number",))
        if len(result.rows) != 1:
            problems.append(
                f"A stat needs a query that returns one row; this returns"
                f" {len(result.rows)}."
            )
        return problems

    x_kinds = ("number", "time") if isinstance(spec, Line) else _DISCRETE
    ok = need("x", spec.x, x_kinds)
    ok = need("y", spec.y, ("number",)) and ok
    if spec.series is not None:
        ok = need("series", spec.series, _DISCRETE) and ok
    if not ok:
        return problems
    if result.truncated:
        problems.append(
            "The query returns too many rows to chart; aggregate or filter it."
        )
    xs = result.values(spec.x)
    series = result.values(spec.series) if spec.series else [None] * len(xs)
    if len(set(zip(xs, series, strict=True))) < len(xs):
        split = f" and {spec.series!r}" if spec.series else ""
        problems.append(
            f"The result has several rows for the same {spec.x!r}{split};"
            " group the query so there is one row each."
        )
    if len(set(series)) > MAX_SERIES:
        problems.append(
            f"{spec.series!r} has {len(set(series))} values; a chart can show at"
            f" most {MAX_SERIES} series. Keep the top few in the query."
        )
    if isinstance(spec, Bar) and len(set(xs)) > MAX_BARS:
        problems.append(
            f"{len(set(xs))} bars is too many; keep at most {MAX_BARS} with"
            " ORDER BY and LIMIT."
        )
    if isinstance(spec, Line) and len(set(xs)) < 2:
        problems.append("A line needs at least two x values; use a stat or bar.")
    return problems


_DISCRETE = ("text", "number", "boolean", "time")
