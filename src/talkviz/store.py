"""What the app remembers, in one SQLite file: chats, their charts, the result
of every chart run, and each chat's feed of events.

A chart row is the chart's current definition; chart_revisions keeps every
definition it has had. A run is a snapshot of one revision's result on one
version of the data, so an old answer can still be shown after either changes.
The feed is append-only: what was said, and which chart run it was about.
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from platformdirs import user_data_path
from pydantic import BaseModel

from talkviz.settings import Settings
from talkviz.sql import ColumnKind, QueryResult
from talkviz.workspace import Chart, Lens, chart_order

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, settings TEXT
);
CREATE TABLE IF NOT EXISTS preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS charts (
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    id TEXT NOT NULL, title TEXT NOT NULL, sql TEXT NOT NULL, spec TEXT NOT NULL,
    revision INTEGER NOT NULL, position INTEGER NOT NULL, view TEXT, lens TEXT,
    parent TEXT, narrow TEXT, based_on INTEGER,
    PRIMARY KEY (chat_id, id)
);
CREATE TABLE IF NOT EXISTS chart_revisions (
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    chart_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL,
    sql TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, chart_id, revision)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    chart_id TEXT NOT NULL, revision INTEGER NOT NULL, data_version TEXT NOT NULL,
    columns TEXT NOT NULL, rows TEXT NOT NULL, truncated INTEGER NOT NULL,
    seconds REAL NOT NULL, ran_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
);
"""


class Chat(BaseModel):
    id: str
    source: str
    title: str
    created_at: str


class Column(BaseModel):
    name: str
    type: str
    kind: ColumnKind


class Run(BaseModel):
    """One chart revision's result on one version of the data."""

    id: int
    chart_id: str
    revision: int
    data_version: str
    columns: list[Column]
    rows: list[list[Any]]
    truncated: bool
    seconds: float
    ran_at: str


class Draft(BaseModel):
    """A reply the model wrote that was not used."""

    text: str
    unsupported: list[str]
    """The figures in it that are not in the query results."""


class Event(BaseModel):
    """One entry in a chat's feed.

    user and assistant events carry `text`. A chart event records that a chart
    was created, updated, or shown, and which run it produced.
    """

    id: int
    at: str
    kind: Literal["user", "assistant", "chart"]
    text: str | None = None
    verified: bool | None = None
    """Assistant only: False if the reply is a fallback or an error message."""
    rejected: list[Draft] | None = None
    """Assistant only: drafts that failed the number check, if any did."""
    error: str | None = None
    """Assistant only: why no plan could be made, if none could."""
    op: str | None = None
    chart_id: str | None = None
    revision: int | None = None
    title: str | None = None
    run_id: int | None = None
    view: str | None = None
    """The kind a set_view event changed the chart to."""
    models: dict[str, str] | None = None
    """Assistant only: the model that did each stage of the turn (route,
    plan, reply). A stage that needed no model is left out."""
    seconds: dict[str, float] | None = None
    """Assistant only: time spent in each stage."""


def default_path() -> Path:
    """talkviz.db in TALKVIZ_HOME, or the user's data directory."""
    home = os.environ.get("TALKVIZ_HOME")
    return (Path(home) if home else user_data_path("talkviz")) / "talkviz.db"


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)
            # Databases made before charts had these.
            have = {row["name"] for row in db.execute("PRAGMA table_info(charts)")}
            for column in ("view", "lens", "parent", "narrow", "based_on"):
                if column not in have:
                    db.execute(f"ALTER TABLE charts ADD COLUMN {column}")
            # And before chats had settings.
            if "settings" not in {
                row["name"] for row in db.execute("PRAGMA table_info(chats)")
            }:
                db.execute("ALTER TABLE chats ADD COLUMN settings TEXT")

    # Chats

    def create_chat(self, source: str, settings: Settings | None = None) -> Chat:
        chat = Chat(
            id=uuid.uuid4().hex[:12], source=source, title="New chat", created_at=_now()
        )
        with self._db() as db:
            db.execute(
                "INSERT INTO chats (id, source, title, created_at, settings)"
                " VALUES (:id, :source, :title, :created_at, :settings)",
                {
                    **chat.model_dump(),
                    "settings": settings.model_dump_json() if settings else None,
                },
            )
        return chat

    # Settings

    def chat_settings(self, chat_id: str) -> Settings | None:
        """The chat's own settings; None for a chat made before chats had any."""
        with self._db() as db:
            row = db.execute(
                "SELECT settings FROM chats WHERE id = ?", [chat_id]
            ).fetchone()
        return Settings.model_validate_json(row[0]) if row and row[0] else None

    def set_chat_settings(self, chat_id: str, settings: Settings) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE chats SET settings = ? WHERE id = ?",
                [settings.model_dump_json(), chat_id],
            )

    def default_settings(self) -> Settings | None:
        """The settings new chats start with, if the user has saved any."""
        with self._db() as db:
            row = db.execute(
                "SELECT value FROM preferences WHERE name = 'settings'"
            ).fetchone()
        return Settings.model_validate_json(row[0]) if row else None

    def set_default_settings(self, settings: Settings) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO preferences VALUES ('settings', ?)"
                " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                [settings.model_dump_json()],
            )

    def chats(self) -> list[Chat]:
        """All chats, newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, source, title, created_at FROM chats"
                " ORDER BY created_at DESC, rowid DESC"
            )
            return [Chat(**row) for row in rows]

    def chat(self, chat_id: str) -> Chat | None:
        with self._db() as db:
            row = db.execute(
                "SELECT id, source, title, created_at FROM chats WHERE id = ?",
                [chat_id],
            ).fetchone()
        return Chat(**row) if row else None

    def rename_chat(self, chat_id: str, title: str) -> None:
        with self._db() as db:
            db.execute("UPDATE chats SET title = ? WHERE id = ?", [title, chat_id])

    def delete_chat(self, chat_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM chats WHERE id = ?", [chat_id])

    # Charts and runs

    def charts(self, chat_id: str) -> list[Chart]:
        """The chat's charts as they are now, in the order they were created,
        each sub-chart after its parent."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, title, sql, spec, revision, view, lens, parent, narrow,"
                " based_on"
                " FROM charts"
                " WHERE chat_id = ?"
                " ORDER BY position",
                [chat_id],
            ).fetchall()
        charts = [
            Chart.model_validate(
                {
                    **dict(row),
                    "spec": json.loads(row["spec"]),
                    "lens": json.loads(row["lens"]) if row["lens"] else None,
                    "narrow": json.loads(row["narrow"]) if row["narrow"] else None,
                }
            )
            for row in rows
        ]
        return sorted(charts, key=chart_order)

    def save_chart(self, chat_id: str, chart: Chart) -> None:
        """Store the chart's current definition, keeping each revision."""
        spec = chart.spec.model_dump_json(exclude_none=True)
        values = [chat_id, chart.id, chart.title, chart.sql, spec, chart.revision]
        narrow = (
            chart.narrow.model_dump_json(exclude_none=True) if chart.narrow else None
        )
        with self._db() as db:
            db.execute(
                "INSERT INTO charts (chat_id, id, title, sql, spec, revision,"
                " position, view, lens, parent, narrow, based_on)"
                " VALUES (?, ?, ?, ?, ?, ?,"
                " (SELECT count(*) FROM charts WHERE chat_id = ?1), ?, ?, ?, ?, ?)"
                " ON CONFLICT (chat_id, id) DO UPDATE SET title = excluded.title,"
                " sql = excluded.sql, spec = excluded.spec,"
                " revision = excluded.revision, view = excluded.view,"
                " lens = excluded.lens, narrow = excluded.narrow,"
                " based_on = excluded.based_on",
                [
                    *values,
                    chart.view,
                    _lens(chart.lens),
                    chart.parent,
                    narrow,
                    chart.based_on,
                ],
            )
            db.execute(
                "INSERT OR IGNORE INTO chart_revisions (chat_id, chart_id, title, sql,"
                " spec, revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [*values, _now()],
            )

    def set_view(self, chat_id: str, chart_id: str, view: str | None) -> None:
        """Record how a chart is drawn. Not a revision: the definition is unchanged."""
        with self._db() as db:
            db.execute(
                "UPDATE charts SET view = ? WHERE chat_id = ? AND id = ?",
                [view, chat_id, chart_id],
            )

    def set_lens(self, chat_id: str, chart_id: str, lens: Lens | None) -> None:
        """Record what part of a chart is in view. Not a revision."""
        with self._db() as db:
            db.execute(
                "UPDATE charts SET lens = ? WHERE chat_id = ? AND id = ?",
                [_lens(lens), chat_id, chart_id],
            )

    def save_run(
        self, chat_id: str, chart: Chart, result: QueryResult, data_version: str
    ) -> Run:
        columns = [
            Column(name=c.name, type=c.type, kind=c.kind) for c in result.columns
        ]
        run = {
            "chat_id": chat_id,
            "chart_id": chart.id,
            "revision": chart.revision,
            "data_version": data_version,
            "columns": json.dumps([c.model_dump() for c in columns]),
            "rows": json.dumps(result.rows, default=_plain),
            "truncated": result.truncated,
            "seconds": result.seconds,
            "ran_at": _now(),
        }
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO runs (chat_id, chart_id, revision, data_version, columns,"
                " rows, truncated, seconds, ran_at) VALUES (:chat_id, :chart_id,"
                " :revision, :data_version, :columns, :rows, :truncated, :seconds,"
                " :ran_at)",
                run,
            )
            row = db.execute(
                "SELECT * FROM runs WHERE id = ?", [cursor.lastrowid]
            ).fetchone()
        return _run(row)

    def latest_runs(self, chat_id: str) -> dict[str, Run]:
        """Each chart's most recent run, by chart id."""
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM runs WHERE id IN (SELECT max(id) FROM runs"
                " WHERE chat_id = ? GROUP BY chart_id)",
                [chat_id],
            ).fetchall()
        return {row["chart_id"]: _run(row) for row in rows}

    def run(self, chat_id: str, run_id: int) -> Run | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM runs WHERE chat_id = ? AND id = ?", [chat_id, run_id]
            ).fetchone()
        return _run(row) if row else None

    # Feed

    def add_event(self, chat_id: str, kind: str, **payload: Any) -> Event:
        at = _now()
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO events (chat_id, at, kind, payload) VALUES (?, ?, ?, ?)",
                [chat_id, at, kind, json.dumps(payload)],
            )
        return Event.model_validate(
            {"id": cursor.lastrowid, "at": at, "kind": kind, **payload}
        )

    def events(self, chat_id: str) -> list[Event]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id, at, kind, payload FROM events WHERE chat_id = ? ORDER BY id",
                [chat_id],
            ).fetchall()
        return [
            Event.model_validate(
                {
                    "id": r["id"],
                    "at": r["at"],
                    "kind": r["kind"],
                    **json.loads(r["payload"]),
                }
            )
            for r in rows
        ]

    @contextmanager
    def _db(self) -> Generator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:  # commit, or roll back on error
                yield db
        finally:
            db.close()


def _run(row: sqlite3.Row) -> Run:
    fields = dict(row)
    fields.pop("chat_id")
    fields["columns"] = json.loads(fields["columns"])
    fields["rows"] = json.loads(fields["rows"])
    return Run.model_validate(fields)


def _lens(lens: Lens | None) -> str | None:
    return lens.model_dump_json() if lens else None


def _plain(value: object) -> object:
    """JSON form of the cell values DuckDB returns that json does not know."""
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
