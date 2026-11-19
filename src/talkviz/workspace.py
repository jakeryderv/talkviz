"""A chat's charts, and the actions the agent can take on them.

A chart is a saved definition (title, SQL, spec), not data: showing it runs
its query against the current database. Changing the definition adds a
revision. Choosing another way to draw the same result (its view) does not.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from talkviz.charts import ChartSpec


class Chart(BaseModel):
    id: str
    title: str
    sql: str
    spec: ChartSpec
    revision: int = 1
    view: str | None = None
    """The kind the chart is drawn as, if not its spec's: one of the kinds
    charts.views() gives for its result. Not part of the definition."""

    @property
    def shown(self) -> str:
        return self.view or self.spec.kind


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


AnyAction = CreateChart | UpdateChart | SetView | ShowChart
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
        changes = action.model_dump(exclude={"op", "chart_id"}, exclude_none=True)
        if not changes:
            return current
        # A new definition starts again in the view its spec names.
        revised = {**changes, "revision": current.revision + 1, "view": None}
        return Chart.model_validate({**current.model_dump(), **revised})

    def apply(self, chart: Chart) -> None:
        """Store a chart from resolve(), replacing the one with the same id."""
        for index, existing in enumerate(self.charts):
            if existing.id == chart.id:
                self.charts[index] = chart
                return
        self.charts.append(chart)
