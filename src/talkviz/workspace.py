"""A chat's charts, and the actions the agent can take on them.

A chart is a saved definition (title, SQL, spec), not data: showing it runs
its query against the current database. Changing the definition adds a
revision. Choosing another way to draw the same result (its view), or moving
around it (its lens), does not.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from talkviz.charts import ChartSpec


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

    lens: Lens | None = None
    """How the chart is zoomed and which series are hidden, if not all of it
    is in view. Not part of the definition."""

    @property
    def shown(self) -> str:
        return self.view or self.spec.kind


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


AnyAction = CreateChart | UpdateChart | SetView | SetLens | ShowChart
Action = Annotated[AnyAction, Field(discriminator="op")]


class Workspace(BaseModel):
    """The charts of one chat, in the order they were created."""

    charts: list[Chart] = []

    def get(self, chart_id: str) -> Chart | None:
        return next((c for c in self.charts if c.id == chart_id), None)

    def resolve(self, action: AnyAction) -> Chart:
        """The chart as it would be after the action. Raises KeyError for an
        unknown chart_id."""
        if isinstance(action, CreateChart):
            return Chart(
                id=f"c{len(self.charts) + 1}",
                title=action.title,
                sql=action.sql,
                spec=action.spec,
            )
        current = self.get(action.chart_id)
        if current is None:
            raise KeyError(action.chart_id)
        if isinstance(action, ShowChart):
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
        revised |= {"view": None, "lens": None}
        return Chart.model_validate({**current.model_dump(), **revised})

    def apply(self, chart: Chart) -> None:
        """Store a chart from resolve(), replacing the one with the same id."""
        for index, existing in enumerate(self.charts):
            if existing.id == chart.id:
                self.charts[index] = chart
                return
        self.charts.append(chart)


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
