"""The description of a data source that the agent is grounded in.

A manifest lists the tables and columns the agent may query, what they mean,
and the caveats its answers must respect. The agent sees the manifest and
nothing else about the source.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Column(_Strict):
    name: str
    type: str
    description: str
    values: list[str] | None = None
    """Every value the column can hold, for columns with a small fixed set."""


class Table(_Strict):
    name: str
    description: str
    columns: list[Column]


class Example(_Strict):
    question: str
    sql: str


class Manifest(_Strict):
    name: str
    title: str
    description: str
    dialect: Literal["duckdb"]
    data_version_sql: str
    """A query returning one value that changes whenever the data does."""
    caveats: list[str] = []
    tables: list[Table]
    examples: list[Example] = []

    @property
    def table_names(self) -> set[str]:
        return {table.name for table in self.tables}


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text()))
