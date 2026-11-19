import pytest
import yaml
from conftest import MANIFEST
from fastapi.testclient import TestClient
from test_llm import Scripted, create

from talkviz.server import create_app
from talkviz.sources import add_source
from talkviz.store import Store

ORDERS = create(
    title="Orders",
    sql="SELECT count(*) AS orders FROM orders",
    spec={"kind": "stat", "value": "orders", "label": "orders"},
)


@pytest.fixture
def source(database, tmp_path, monkeypatch):
    monkeypatch.setenv("TALKVIZ_HOME", str(tmp_path / "home"))
    manifest = tmp_path / "shop.yaml"
    manifest.write_text(yaml.safe_dump(MANIFEST))
    return add_source(database, manifest)


def client(*replies):
    return TestClient(create_app(model=Scripted(*replies), store=Store()))


def test_sources_are_listed_with_their_data_version(source):
    [listed] = client().get("/api/sources").json()
    assert (listed["name"], listed["title"]) == ("shop", "Shop orders")
    assert listed["data_version"] == "2024-03-08"


def test_a_turn_adds_a_chart_its_result_and_the_feed(source):
    app = client({"actions": [create()]}, "Games lead at 105.")
    chat = app.post("/api/chats", json={}).json()
    assert chat["title"] == "New chat"
    turn = app.post(
        f"/api/chats/{chat['id']}/turns", json={"text": "sales by category?"}
    )
    assert turn.status_code == 200, turn.text
    turn = turn.json()
    assert turn["chat"]["title"] == "sales by category?"
    assert [e["kind"] for e in turn["events"]] == ["user", "chart", "assistant"]
    assert turn["events"][1]["op"] == "create_chart"
    assert turn["shown"] == ["c1"]
    [chart] = turn["charts"]
    assert chart["spec"] == {
        "kind": "bar",
        "x": "category",
        "y": "total",
        "series": None,
    }
    run = chart["run"]
    assert run["data_version"] == "2024-03-08"
    assert [(c["name"], c["kind"]) for c in run["columns"]] == [
        ("category", "text"),
        ("total", "number"),
    ]
    assert sorted(run["rows"]) == [["books", 40.5], ["games", 105.0], ["toys", 35.0]]
    assert turn["events"][1]["run_id"] == run["id"]


def test_a_chat_is_restored_with_charts_results_and_feed(source):
    app = client(
        {"actions": [create()]},
        "Games lead at 105.",
        {"actions": [ORDERS]},
        "There are 6 orders.",
        {
            "actions": [
                {"op": "update_chart", "chart_id": "c1", "spec": {"kind": "table"}}
            ]
        },
    )
    chat_id = app.post("/api/chats", json={}).json()["id"]
    for text in ("sales by category?", "how many orders?", "make the first a table"):
        app.post(f"/api/chats/{chat_id}/turns", json={"text": text})
    view = app.get(f"/api/chats/{chat_id}").json()
    assert [
        (c["id"], c["title"], c["spec"]["kind"], c["view"], c["revision"])
        for c in view["charts"]
    ] == [
        ("c1", "Sales by category", "bar", "table", 1),
        ("c2", "Orders", "stat", None, 1),
    ]
    assert view["charts"][1]["run"]["rows"] == [[6]]
    assert view["events"][-1]["text"] == "Here's Sales by category as a table."
    assert view["shown"] == ["c1"]
    assert [e["kind"] for e in view["events"]] == ["user", "chart", "assistant"] * 3
    assert [c["title"] for c in app.get("/api/chats").json()] == ["sales by category?"]


def test_later_turns_are_told_the_conversation_and_charts_so_far(source):
    model = Scripted(
        {"actions": [create()]},
        "Games lead at 105.",
        {"actions": [ORDERS]},
        "6 orders.",
    )
    app = TestClient(create_app(model=model, store=Store()))
    chat_id = app.post("/api/chats", json={}).json()["id"]
    app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales by category?"})
    app.post(f"/api/chats/{chat_id}/turns", json={"text": "how many orders?"})
    prompt = model.sent(2)
    assert "User: sales by category?" in prompt
    assert "You: Games lead at 105." in prompt
    assert "c1: 'Sales by category'" in prompt


def test_rerunning_a_chart_adds_a_run_but_nothing_to_the_feed(source):
    app = client({"actions": [create()]}, "Games lead at 105.")
    chat_id = app.post("/api/chats", json={}).json()["id"]
    first = app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales?"}).json()
    again = app.post(f"/api/chats/{chat_id}/charts/c1/run").json()
    assert again["run"]["id"] > first["charts"][0]["run"]["id"]
    # The query has no ORDER BY, so row order can differ between runs.
    assert sorted(again["run"]["rows"]) == sorted(first["charts"][0]["run"]["rows"])
    assert len(app.get(f"/api/chats/{chat_id}").json()["events"]) == 3
    assert app.post(f"/api/chats/{chat_id}/charts/c9/run").status_code == 404


def test_a_turn_the_model_cannot_answer_is_recorded_as_an_apology(source):
    bad = {"actions": [create(sql="SELECT * FROM secrets")]}
    app = client(bad, bad, bad)
    chat_id = app.post("/api/chats", json={}).json()["id"]
    turn = app.post(f"/api/chats/{chat_id}/turns", json={"text": "secrets?"}).json()
    assert [e["kind"] for e in turn["events"]] == ["user", "assistant"]
    assert turn["events"][1]["verified"] is False
    assert turn["charts"] == []


def test_chats_can_be_renamed_and_deleted(source):
    app = client({"actions": [create()]}, "Games lead at 105.")
    chat_id = app.post("/api/chats", json={}).json()["id"]
    app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales?"})
    assert (
        app.patch(f"/api/chats/{chat_id}", json={"title": "Sales"}).json()["title"]
        == "Sales"
    )
    assert app.delete(f"/api/chats/{chat_id}").status_code == 204
    assert app.get(f"/api/chats/{chat_id}").status_code == 404
    assert app.get("/api/chats").json() == []
    # Deleting a chat deletes its charts, runs, and feed.
    assert Store().charts(chat_id) == [] and Store().events(chat_id) == []


def test_bad_requests_are_explained(source):
    app = client()
    assert app.post("/api/chats", json={"source": "cars"}).status_code == 400
    assert app.get("/api/chats/nope").status_code == 404
    chat_id = app.post("/api/chats", json={}).json()["id"]
    assert app.post(f"/api/chats/{chat_id}/turns", json={"text": ""}).status_code == 422


def test_a_view_is_saved_without_a_revision_query_or_feed_entry(source):
    app = client({"actions": [create()]}, "Games lead at 105.")
    chat_id = app.post("/api/chats", json={}).json()["id"]
    app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales by category?"})
    before = app.get(f"/api/chats/{chat_id}").json()
    path = f"/api/chats/{chat_id}/charts/c1/view"

    assert app.put(path, json={"kind": "donut"}).json()["view"] == "donut"
    after = app.get(f"/api/chats/{chat_id}").json()
    chart = after["charts"][0]
    assert (chart["view"], chart["spec"]["kind"], chart["revision"]) == (
        "donut",
        "bar",
        1,
    )
    assert chart["run"]["id"] == before["charts"][0]["run"]["id"]
    assert after["events"] == before["events"]

    # The spec's own kind is the default; kinds the result does not fit are refused.
    assert app.put(path, json={"kind": "bar"}).json()["view"] is None
    assert app.put(path, json={"kind": "scatter3d"}).status_code == 400
    assert (
        app.put(
            f"/api/chats/{chat_id}/charts/c9/view", json={"kind": "bar"}
        ).status_code
        == 404
    )


def test_the_charts_of_the_latest_turn_are_shown_together(source):
    app = client(
        {"actions": [create()]},
        "Games lead at 105.",
        {"actions": [ORDERS]},
        "There are 6 orders.",
        {
            "actions": [
                {"op": "show_chart", "chart_id": "c1"},
                {"op": "show_chart", "chart_id": "c2"},
            ]
        },
    )
    chat_id = app.post("/api/chats", json={}).json()["id"]
    for text in ("sales by category?", "how many orders?"):
        turn = app.post(f"/api/chats/{chat_id}/turns", json={"text": text}).json()
    assert turn["shown"] == ["c2"]
    turn = app.post(f"/api/chats/{chat_id}/turns", json={"text": "both"}).json()
    assert turn["shown"] == ["c1", "c2"]
    assert (
        turn["events"][-1]["text"]
        == "Here are Sales by category and Orders, side by side."
    )
    assert app.get(f"/api/chats/{chat_id}").json()["shown"] == ["c1", "c2"]


def test_an_unverified_reply_keeps_the_drafts_and_figures_that_failed(source):
    app = client({"actions": [create()]}, *["Games made 999."] * 3)
    chat_id = app.post("/api/chats", json={}).json()["id"]
    turn = app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales?"}).json()
    reply = turn["events"][-1]
    assert (reply["text"], reply["verified"]) == ("Here is Sales by category.", False)
    assert (
        reply["rejected"] == [{"text": "Games made 999.", "unsupported": ["999"]}] * 3
    )
    assert (
        app.get(f"/api/chats/{chat_id}").json()["events"][-1]["rejected"]
        == reply["rejected"]
    )
