"""Registered data sources: a database file plus the manifest describing it."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import duckdb
from platformdirs import user_config_path

from talkviz.manifest import Manifest, load_manifest
from talkviz.sql import connect


@dataclass(frozen=True)
class Source:
    name: str
    database: Path
    manifest: Manifest

    def connect(self) -> duckdb.DuckDBPyConnection:
        return connect(self.database)

    def data_version(self, con: duckdb.DuckDBPyConnection) -> str:
        return str(con.execute(self.manifest.data_version_sql).fetchall()[0][0])


def registry_path() -> Path:
    """sources.json in TALKVIZ_HOME, or the user's config directory."""
    home = os.environ.get("TALKVIZ_HOME")
    return (Path(home) if home else user_config_path("talkviz")) / "sources.json"


def add_source(database: Path, manifest: Path) -> Source:
    """Register a source under its manifest's name, replacing any of that name."""
    database, manifest = database.resolve(), manifest.resolve()
    if not database.is_file():
        raise FileNotFoundError(f"No database at {database}")
    name = load_manifest(manifest).name
    entries = _read()
    entries[name] = {"database": str(database), "manifest": str(manifest)}
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n")
    return load_source(name)


def list_sources() -> list[str]:
    return sorted(_read())


def load_source(name: str | None = None) -> Source:
    """The named source, or the only one registered if no name is given."""
    entries = _read()
    if name is None:
        if len(entries) != 1:
            known = ", ".join(sorted(entries)) or "none registered"
            raise LookupError(f"Say which source to use ({known}).")
        name = next(iter(entries))
    if name not in entries:
        raise LookupError(f"No source named {name!r}. Add one with `talkviz add`.")
    entry = entries[name]
    return Source(name, Path(entry["database"]), load_manifest(Path(entry["manifest"])))


def _read() -> dict[str, dict[str, str]]:
    path = registry_path()
    return json.loads(path.read_text()) if path.exists() else {}
