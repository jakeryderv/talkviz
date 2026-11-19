"""A chat's charts, and the actions the agent can take on them.

A chart is a saved definition (title, SQL, spec), not data: showing it runs
its query against the current database. Editing a chart adds a revision.
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


class ShowChart(_Action):
    """Bring an existing chart back into focus, re-run on current data."""

    op: Literal["show_chart"]
    chart_id: str


Action = Annotated[CreateChart | UpdateChart | ShowChart, Field(discriminator="op")]


class Workspace(BaseModel):
    """The charts of one chat, in the order they were created."""

    charts: list[Chart] = []

    def get(self, chart_id: str) -> Chart | None:
        return next((c for c in self.charts if c.id == chart_id), None)

    def resolve(self, action: CreateChart | UpdateChart | ShowChart) -> Chart:
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
        changes = action.model_dump(exclude={"op", "chart_id"}, exclude_none=True)
        if not changes:
            return current
        return Chart.model_validate(
            {**current.model_dump(), **changes, "revision": current.revision + 1}
        )

    def apply(self, chart: Chart) -> None:
        """Store a chart from resolve(), replacing the one with the same id."""
        for index, existing in enumerate(self.charts):
            if existing.id == chart.id:
                self.charts[index] = chart
                return
        self.charts.append(chart)
