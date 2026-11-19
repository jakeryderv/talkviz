import pytest
import yaml
from conftest import MANIFEST

from talkviz.sources import add_source, list_sources, load_source


@pytest.fixture
def manifest_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TALKVIZ_HOME", str(tmp_path / "home"))
    path = tmp_path / "shop.yaml"
    path.write_text(yaml.safe_dump(MANIFEST))
    return path


def test_added_sources_can_be_listed_loaded_and_queried(database, manifest_file):
    assert list_sources() == []
    add_source(database, manifest_file)
    assert list_sources() == ["shop"]
    source = load_source()  # the only one, so no name is needed
    assert source.manifest.title == "Shop orders"
    with source.connect() as con:
        assert source.data_version(con) == "2024-03-08"


def test_loading_explains_what_is_missing(database, manifest_file, tmp_path):
    with pytest.raises(LookupError, match="none registered"):
        load_source()
    add_source(database, manifest_file)
    with pytest.raises(LookupError, match="No source named 'cars'"):
        load_source("cars")
    with pytest.raises(FileNotFoundError):
        add_source(tmp_path / "missing.duckdb", manifest_file)


def test_manifests_with_unknown_fields_are_rejected(database, manifest_file):
    manifest_file.write_text(yaml.safe_dump({**MANIFEST, "tabels": []}))
    with pytest.raises(ValueError, match="tabels"):
        add_source(database, manifest_file)
