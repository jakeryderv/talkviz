"""A chat's charts, and the actions the agent can take on them.

A chart is a saved definition (title, SQL, spec), not data: showing it runs
its query against the current database. Changing the definition adds a
revision. Choosing another way to draw the same result (its view), or moving
around it (its lens), does not.
"""

from typing import Annotated, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from talkviz.charts import ChartSpec, Condition, Narrow, narrowed
from talkviz.manifest import Manifest
from talkviz.sql import QueryResult, check_and_run


class Lens(BaseModel):
    """The part of a chart in view. Values are as charts.axis() gives them."""

    start: str | None = None
    end: str | None = None
    """The first and last x values in view, both or neither."""
    hidden: list[str] = []
    """Series not drawn."""


class Chart(BaseModel):
    id: str
    title: str
    sql: str
    spec: ChartSpec
    revision: int = 1
    view: str | None = None
    """The kind the chart is drawn as, if not its spec's: one of the kinds
    charts.views() gives for its result. Not part of the definition."""

    parent: str | None = None
    """Id of the chart this one was made from, if it is a sub-chart. Sub-charts
    are one level deep and named after their parent: c1a, c1b, ..."""
    based_on: int | None = None
    """The parent's revision when a sub-chart with its own query was made or
    last changed. If the parent has moved on, the sub-chart may be stale."""
    narrow: Narrow | None = None
    """For a sub-chart that selects rows of its parent's result. Then `sql`
    is the parent's, and running the chart runs that and keeps the rows. A
    sub-chart without it has a query of its own."""
    lens: Lens | None = None
    """How the chart is zoomed and which series are hidden, if not all of it
    is in view. Not part of the definition."""

    @property
    def shown(self) -> str:
        return self.view or self.spec.kind

    @property
    def label(self) -> str:
        """What the user calls it: "1", or "1a" for a sub-chart."""
        return self.id.removeprefix("c")


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetLens(_Action):
    """Move around an existing chart without changing its data: zoom to a
    range along its x axis, hide or bring back series, or reset."""

    op: Literal["set_lens"]
    chart_id: str
    start: str | None = Field(None, description="x value the zoom starts at.")
    end: str | None = Field(None, description="x value the zoom ends at.")
    hide: list[str] = Field(default=[], description="Series to hide.")
    show: list[str] = Field(default=[], description="Hidden series to bring back.")
    zoom_out: bool = Field(
        False, description="Show the whole x axis again; hidden series stay hidden."
    )
    reset: bool = Field(False, description="Undo all zooming and hiding first.")


class CreateChart(_Action):
    """Add a new chart to the chat."""

    op: Literal["create_chart"]
    title: str = Field(description="Short and distinct from the chat's other titles.")
    sql: str
    spec: ChartSpec
    parent: str | None = Field(
        None,
        description=(
            "Id of the existing chart this one filters, when its rows cannot be"
            " picked from that chart's result and a new query is needed."
        ),
    )


class NarrowChart(_Action):
    """Make a sub-chart that keeps some rows of an existing chart's result."""

    op: Literal["narrow_chart"]
    chart_id: str = Field(description="The chart to take rows from.")
    title: str = Field(description="Short; says what was kept.")
    where: list[Condition] = []
    top: int | None = None
    by: str | None = None
    spec: ChartSpec | None = Field(
        None, description="Only if it should be drawn differently from its parent."
    )


class UpdateChart(_Action):
    """Change an existing chart in place. Give only the fields that change."""

    op: Literal["update_chart"]
    chart_id: str
    title: str | None = None
    sql: str | None = None
    spec: ChartSpec | None = None


class SetView(_Action):
    """Draw an existing chart's result another way, without changing the chart."""

    op: Literal["set_view"]
    chart_id: str
    kind: str = Field(description="Chart kind to draw it as, e.g. donut or table.")


class ShowChart(_Action):
    """Bring an existing chart back into focus, re-run on current data."""

    op: Literal["show_chart"]
    chart_id: str


class Discuss(_Action):
    """Answer a question about what an existing chart shows. Changes nothing."""

    op: Literal["discuss"]
    chart_id: str


class RevertChart(_Action):
    """Put a chart back to an earlier definition: undo. The earlier definition
    becomes a new revision, so nothing is lost and it can itself be undone."""

    op: Literal["revert_chart"]
    chart_id: str
    revision: int | None = Field(
        default=None,
        description="The revision to go back to. Leave out for the one before"
        " the chart's current revision.",
    )


AnyAction = (
    CreateChart
    | NarrowChart
    | UpdateChart
    | RevertChart
    | SetView
    | SetLens
    | ShowChart
    | Discuss
)


class Past(BaseModel):
    """A definition a chart has had."""

    revision: int
    title: str
    sql: str
    spec: ChartSpec
    narrow: Narrow | None = None
    at: str = ""
    """When it was made."""


Action = Annotated[AnyAction, Field(discriminator="op")]


class Workspace(BaseModel):
    """The charts of one chat, in the order they were created."""

    charts: list[Chart] = []
    past: dict[str, list[Past]] = {}
    """Every definition each chart has had, oldest first, for going back."""

    def get(self, chart_id: str) -> Chart | None:
        return next((c for c in self.charts if c.id == chart_id), None)

    def resolve(self, action: AnyAction) -> Chart:
        """The chart as it would be after the action. Raises KeyError for an
        unknown chart_id."""
        if isinstance(action, CreateChart):
            parent = None
            if action.parent is None:
                top = sum(chart.parent is None for chart in self.charts)
                new = f"c{top + 1}"
            else:
                new = self.sub_id(action.parent)
                parent = self.get(action.parent)
            return Chart(
                id=new,
                title=action.title,
                sql=action.sql,
                spec=action.spec,
                parent=action.parent,
                based_on=parent.revision if parent else None,
            )
        current = self.get(action.chart_id)
        if current is None:
            raise KeyError(action.chart_id)
        if isinstance(action, NarrowChart):
            return Chart(
                id=self.sub_id(current.id),
                title=action.title,
                sql=current.sql,
                spec=action.spec or current.spec,
                parent=current.id,
                narrow=Narrow(where=action.where, top=action.top, by=action.by),
            )
        if isinstance(action, RevertChart):
            wanted = action.revision or current.revision - 1
            known = {past.revision: past for past in self.past.get(current.id, [])}
            if wanted == current.revision or wanted not in known:
                raise NoPast(
                    f"Chart {current.label} has no earlier version to go back to."
                    if action.revision is None
                    else f"Chart {current.label} has no version {wanted} to go back to."
                )
            parent = self.get(current.parent or "")
            restored = known[wanted].model_dump(exclude={"revision", "at"}) | {
                "revision": current.revision + 1,
                "based_on": parent.revision if parent else None,
                "view": None,
                "lens": None,
            }
            return Chart.model_validate({**current.model_dump(), **restored})
        if isinstance(action, ShowChart | Discuss):
            return current
        if isinstance(action, SetView):
            # The spec's own kind is the default, stored as no view.
            view = None if action.kind == current.spec.kind else action.kind
            return current.model_copy(update={"view": view})
        if isinstance(action, SetLens):
            return current.model_copy(update={"lens": _moved(current.lens, action)})
        changes = action.model_dump(exclude={"op", "chart_id"}, exclude_none=True)
        if not changes:
            return current
        # A new definition starts again in the view its spec names, in full.
        revised = {**changes, "revision": current.revision + 1}
        if "sql" in changes:
            revised["narrow"] = None  # a query of its own now
            parent = self.get(current.parent or "")
            revised["based_on"] = parent.revision if parent else None
        revised |= {"view": None, "lens": None}
        return Chart.model_validate({**current.model_dump(), **revised})

    def sub_id(self, parent_id: str) -> str:
        """The id the next sub-chart of `parent_id` would have. Raises KeyError
        for an unknown chart and NoRoom if it cannot have one."""
        parent = self.get(parent_id)
        if parent is None:
            raise KeyError(parent_id)
        if parent.parent is not None:
            raise NoRoom(
                f"Chart {parent.label} is itself a sub-chart, and sub-charts"
                " cannot have sub-charts of their own."
            )
        taken = sum(chart.parent == parent_id for chart in self.charts)
        if taken >= len(LETTERS):
            raise NoRoom(f"Chart {parent.label} already has {taken} sub-charts.")
        return f"{parent_id}{LETTERS[taken]}"

    def source(self, chart: Chart) -> str:
        """The query to run for the chart: its parent's, if it narrows one."""
        parent = self.get(chart.parent or "") if chart.narrow else None
        return parent.sql if parent else chart.sql

    def apply(self, chart: Chart) -> None:
        """Store a chart from resolve(), replacing the one with the same id.
        Sub-charts are kept after their parent."""
        for index, existing in enumerate(self.charts):
            if existing.id == chart.id:
                self.charts[index] = chart
                return
        self.charts.append(chart)
        self.charts.sort(key=chart_order)


LETTERS = "abcdefghijklmnopqrstuvwxyz"


class NoRoom(Exception):
    """A chart cannot be given a sub-chart; the message says why."""


class NoPast(Exception):
    """A chart cannot be put back as asked; the message says why."""


def chart_order(chart: Chart) -> tuple[int, str]:
    """Sort key that puts each sub-chart after its parent: c2, c2a, c2b, c10."""
    top = (chart.parent or chart.id).removeprefix("c")
    return int(top), chart.id


def run_chart(
    con: duckdb.DuckDBPyConnection,
    manifest: Manifest,
    workspace: Workspace,
    chart: Chart,
) -> QueryResult:
    """Run the chart's query and, for a narrowing sub-chart, keep its rows.
    Raises QueryRejected, or ValueError if the narrowing no longer applies."""
    result = check_and_run(con, workspace.source(chart), manifest)
    return narrowed(result, chart.narrow) if chart.narrow else result


def _moved(lens: Lens | None, action: SetLens) -> Lens | None:
    """The lens after the action, or None if the whole chart is in view."""
    base = Lens() if action.reset or lens is None else lens
    if action.zoom_out:
        base = Lens(hidden=base.hidden)
    hidden = [name for name in [*base.hidden, *action.hide] if name not in action.show]
    moved = Lens(
        start=action.start if action.start is not None else base.start,
        end=action.end if action.end is not None else base.end,
        hidden=list(dict.fromkeys(hidden)),
    )
    return None if moved == Lens() else moved
