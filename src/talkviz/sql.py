"""Check and run the read-only queries the agent writes.

A query goes through three stages, each of which can reject it:

1. lint():     one SELECT statement that reads only the manifest's tables
2. describe(): DuckDB plans it without running it, which checks names and
               types and gives the result's columns
3. run():      executed with a row limit and a timeout

Connections come from connect(), which opens the database read-only with file
and network access switched off, so the engine enforces what lint() checks.
"""

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import duckdb
import sqlglot
from sqlglot import exp

from talkviz.manifest import Manifest

MAX_ROWS = 2000
TIMEOUT_SECONDS = 20.0

ColumnKind = Literal["number", "time", "text", "boolean", "other"]


class QueryRejected(Exception):
    """The query is not allowed or does not work; the message says why."""


@dataclass(frozen=True)
class ResultColumn:
    name: str
    type: str

    @property
    def kind(self) -> ColumnKind:
        base = self.type.split("(")[0].upper()
        if base in _NUMBER_TYPES:
            return "number"
        if base.startswith(("DATE", "TIMESTAMP", "TIME")):
            return "time"
        if base == "VARCHAR":
            return "text"
        return "boolean" if base == "BOOLEAN" else "other"


@dataclass(frozen=True)
class QueryResult:
    columns: list[ResultColumn]
    rows: list[tuple[Any, ...]]
    truncated: bool
    """True if the query had more than MAX_ROWS rows; `rows` holds the first."""
    seconds: float

    def column(self, name: str) -> ResultColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    def values(self, name: str) -> list[Any]:
        index = [c.name for c in self.columns].index(name)
        return [row[index] for row in self.rows]


def connect(database: Path) -> duckdb.DuckDBPyConnection:
    """A connection that can only read `database`: no writes, files, or network."""
    con = duckdb.connect(
        database, read_only=True, config={"enable_external_access": False}
    )
    try:
        con.execute("SET lock_configuration = true")
    except duckdb.InvalidInputException:
        # Another open connection to this database already locked it; they
        # share one configuration, so this one is restricted the same way.
        pass
    return con


def lint(sql: str, manifest: Manifest) -> None:
    """Reject anything but a single SELECT over the manifest's tables."""
    try:
        statements = sqlglot.parse(sql, read=manifest.dialect)
    except sqlglot.errors.SqlglotError as error:
        raise QueryRejected(f"SQL does not parse: {error}") from error
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise QueryRejected("Write exactly one SQL statement.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise QueryRejected("Only SELECT queries are allowed.")
    forbidden = tree.find(*_FORBIDDEN)
    if forbidden is not None:
        raise QueryRejected(
            f"{forbidden.key.upper()} is not allowed; write a plain SELECT query."
        )
    for function in tree.find_all(exp.Anonymous):
        if function.name.lower() in _FORBIDDEN_FUNCTIONS:
            raise QueryRejected(f"The function {function.name}() is not allowed.")
    local_names = {cte.alias for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise QueryRejected(
                f"Table functions are not allowed: {table.sql(manifest.dialect)}"
            )
        if table.db or table.catalog:
            raise QueryRejected(
                f"Use bare table names, not {table.sql(manifest.dialect)}."
            )
        if table.name not in manifest.table_names | local_names:
            raise QueryRejected(
                f"Unknown table {table.name!r}. The tables are:"
                f" {', '.join(sorted(manifest.table_names))}."
            )


def describe(con: duckdb.DuckDBPyConnection, sql: str) -> list[ResultColumn]:
    """The columns the query would return, without running it."""
    try:
        described = con.execute(f"DESCRIBE {sql}").fetchall()
    except duckdb.Error as error:
        raise QueryRejected(str(error)) from error
    return [ResultColumn(name, dtype) for name, dtype, *_ in described]


def run(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int = MAX_ROWS,
    timeout: float = TIMEOUT_SECONDS,
) -> QueryResult:
    """Run a query that passed lint(), keeping at most max_rows rows."""
    columns = describe(con, sql)
    timer = threading.Timer(timeout, con.interrupt)
    started = time.perf_counter()
    timer.start()
    try:
        rows = con.execute(sql).fetchmany(max_rows + 1)
    except duckdb.InterruptException as error:
        raise QueryRejected(
            f"The query took longer than {timeout:g} seconds; make it simpler."
        ) from error
    except duckdb.Error as error:
        raise QueryRejected(str(error)) from error
    finally:
        timer.cancel()
    return QueryResult(
        columns=columns,
        rows=rows[:max_rows],
        truncated=len(rows) > max_rows,
        seconds=time.perf_counter() - started,
    )


def check_and_run(
    con: duckdb.DuckDBPyConnection, sql: str, manifest: Manifest
) -> QueryResult:
    lint(sql, manifest)
    return run(con, sql)


_NUMBER_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
}
# Statement kinds that can appear nested inside a query or change state.
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Copy,
    exp.Attach,
    exp.Detach,
    exp.Pragma,
    exp.Set,
    exp.Use,
    exp.Command,
    exp.Into,
)
# Functions that reveal the host rather than the data: settings (which include
# local paths), environment variables, and the query text itself.
_FORBIDDEN_FUNCTIONS = {"current_setting", "getenv", "current_query"}
