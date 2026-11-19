"""The model calls, driven by scripted models instead of a real one."""

import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from talkviz import llm
from talkviz.llm import Exchange
from talkviz.turn import take_turn
from talkviz.workspace import Workspace

TOTALS = "SELECT category, sum(amount) AS total FROM orders GROUP BY category"


def create(title="Sales by category", sql=TOTALS, spec=None):
    spec = spec or {"kind": "bar", "x": "category", "y": "total"}
    return {"op": "create_chart", "title": title, "sql": sql, "spec": spec}


class Scripted(FunctionModel):
    """A model that gives the replies in order and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen: list[list[ModelMessage]] = []
        super().__init__(self.respond)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(list(messages))
        reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return ModelResponse(parts=[TextPart(text)])

    def sent(self, call: int) -> str:
        """The latest message sent to the model before its call-th reply."""
        return str(getattr(self.seen[call][-1].parts[-1], "content", ""))


scripted = Scripted


def test_openrouter_sets_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.delenv("TALKVIZ_EFFORT", raising=False)
    assert not (llm.openrouter("some/model").settings or {})
    model = llm.openrouter("some/model", "low")
    assert model.settings == {"openrouter_reasoning": {"effort": "low"}}
    monkeypatch.setenv("TALKVIZ_EFFORT", "none")
    model = llm.openrouter("some/model")
    assert model.settings == {"openrouter_reasoning": {"effort": "none"}}
    with pytest.raises(ValueError, match="unknown reasoning effort"):
        llm.openrouter("some/model", "lots")


def test_default_models_come_with_their_tuned_effort(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    for name in (
        "TALKVIZ_MODEL",
        "TALKVIZ_EFFORT",
        "TALKVIZ_SPEECH_MODEL",
        "TALKVIZ_SPEECH_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)
    planner, speaker = llm.openrouter(), llm.speech_openrouter()
    assert planner.model_name == llm.DEFAULT_MODEL
    assert planner.settings == {"openrouter_reasoning": {"effort": llm.DEFAULT_EFFORT}}
    assert speaker is not None and speaker.model_name == llm.DEFAULT_SPEECH_MODEL
    assert speaker.settings == {
        "openrouter_reasoning": {"effort": llm.DEFAULT_SPEECH_EFFORT}
    }
    # A model the user picks keeps its own default effort.
    monkeypatch.setenv("TALKVIZ_MODEL", "other/model")
    assert not (llm.openrouter().settings or {})


def test_speech_openrouter_is_none_unless_a_speech_model_is_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(llm, "DEFAULT_SPEECH_MODEL", None)
    monkeypatch.delenv("TALKVIZ_SPEECH_MODEL", raising=False)
    monkeypatch.setenv("TALKVIZ_SPEECH_EFFORT", "none")
    monkeypatch.setenv("TALKVIZ_EFFORT", "high")
    assert llm.speech_openrouter() is None
    monkeypatch.setenv("TALKVIZ_SPEECH_MODEL", "small/model")
    speaker = llm.speech_openrouter()
    assert speaker is not None and speaker.model_name == "small/model"
    # The planner's effort does not leak into the speaker's.
    assert speaker.settings == {"openrouter_reasoning": {"effort": "none"}}


def test_plan_runs_the_query_and_returns_the_chart(manifest, con):
    model = scripted({"actions": [create()]})
    planned = llm.plan(model, "sales by category?", manifest, con, Workspace(), [])
    [run] = planned.runs
    assert (run.chart.id, run.chart.revision, run.op) == ("c1", 1, "create_chart")
    assert sorted(run.result.rows) == [
        ("books", 40.5),
        ("games", 105.0),
        ("toys", 35.0),
    ]


def test_plan_instructions_ground_the_model_in_the_manifest(manifest, con):
    model = scripted({"actions": [create()]})
    llm.plan(model, "sales?", manifest, con, Workspace(), [])
    instructions = getattr(model.seen[0][0], "instructions", None)
    assert instructions is not None
    for expected in (
        "Shop orders",
        "### orders",
        "amount (DOUBLE)",
        "books, games, toys",
        "Refunds are not included.",
    ):
        assert expected in instructions


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (create(sql="SELECT token FROM secrets"), "Unknown table 'secrets'"),
        (create(sql="DELETE FROM orders"), "Only SELECT"),
        (create(sql="SELECT category, price FROM orders"), "price"),
        (
            create(spec={"kind": "line", "x": "category", "y": "total"}),
            "must be number or time",
        ),
        (
            create(spec={"kind": "bar", "x": "category", "y": "sales"}),
            "'sales' is not in",
        ),
        ({"op": "show_chart", "chart_id": "c9"}, "no chart 'c9'"),
    ],
)
def test_plan_sends_problems_back_and_accepts_the_fix(bad, reason, manifest, con):
    model = scripted({"actions": [bad]}, {"actions": [create()]})
    planned = llm.plan(model, "sales by category?", manifest, con, Workspace(), [])
    assert reason in model.sent(1)
    assert [run.chart.title for run in planned.runs] == ["Sales by category"]


def test_plan_gives_up_after_repeated_failures(manifest, con):
    bad = {"actions": [create(sql="SELECT * FROM secrets")]}
    with pytest.raises(llm.ModelFailed):
        llm.plan(scripted(bad, bad, bad), "secrets?", manifest, con, Workspace(), [])


def test_plan_may_decline_with_a_note_but_not_with_nothing(manifest, con):
    model = scripted(
        {"actions": []}, {"actions": [], "note": "There is no weather data."}
    )
    planned = llm.plan(model, "what's the weather?", manifest, con, Workspace(), [])
    assert planned.runs == []
    assert planned.plan.note == "There is no weather data."
    assert "at least one action" in model.sent(1)


def test_speak_rejects_numbers_that_are_not_in_the_results(manifest, con):
    planner = scripted({"actions": [create()]})
    planned = llm.plan(planner, "sales by category?", manifest, con, Workspace(), [])
    speaker = scripted("Games lead with 250 in sales.", "Games lead with 105 in sales.")
    spoken = llm.speak(speaker, "sales by category?", manifest, planned, [])
    assert (spoken.text, spoken.verified) == ("Games lead with 105 in sales.", True)
    assert "250" in speaker.sent(1)
    # The results reached the model.
    assert "games | 105.0" in speaker.sent(0)


def test_speak_may_use_numbers_from_the_caveats(manifest, con):
    manifest = manifest.model_copy(
        update={"caveats": ["Orders before March 2024 may be missing."]}
    )
    planner = scripted({"actions": [create()]})
    planned = llm.plan(planner, "sales?", manifest, con, Workspace(), [])
    speaker = scripted("Games lead with 105, though orders before 2024 may be missing.")
    assert llm.speak(speaker, "sales?", manifest, planned, []).verified
    assert len(speaker.seen) == 1


def test_speak_falls_back_when_the_numbers_never_check_out(manifest, con):
    planner = scripted({"actions": [create()]})
    planned = llm.plan(planner, "sales?", manifest, con, Workspace(), [])
    speaker = scripted(*["Games made 999."] * 3)
    spoken = llm.speak(speaker, "sales?", manifest, planned, [])
    assert (spoken.text, spoken.verified) == ("Here is Sales by category.", False)


class Streamed(FunctionModel):
    """A streaming model that sends each reply in the given pieces."""

    def __init__(self, *replies: list[str]):
        self.replies = list(replies)
        self.seen: list[list[ModelMessage]] = []
        super().__init__(stream_function=self.stream)

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        self.seen.append(list(messages))
        for piece in self.replies.pop(0):
            yield piece

    sent = Scripted.sent


def stream_reply(speaker, manifest, con):
    question = "sales by category?"
    planner = scripted({"actions": [create()]})
    planned = llm.plan(planner, question, manifest, con, Workspace(), [])
    *chunks, spoken = llm.speak_stream(speaker, question, manifest, planned, [])
    assert all(isinstance(chunk, str) for chunk in chunks)
    assert isinstance(spoken, llm.Spoken)
    assert spoken.text == "".join(str(chunk) for chunk in chunks)
    return chunks, spoken


def test_speak_stream_yields_checked_sentences_then_the_result(manifest, con):
    speaker = Streamed(
        ["Games le", "ad with 105.", " Books follow at 4", "0.5 in sales."]
    )
    chunks, spoken = stream_reply(speaker, manifest, con)
    assert chunks == ["Games lead with 105.", " Books follow at 40.5 in sales."]
    assert spoken.verified
    assert "games | 105.0" in speaker.sent(0)


def test_speak_stream_retries_when_the_first_sentence_fails(manifest, con):
    speaker = Streamed(["Games made 250. ", "Never sent."], ["Games made 105."])
    chunks, spoken = stream_reply(speaker, manifest, con)
    assert (chunks, spoken.verified) == (["Games made 105."], True)
    assert "not in the query results: 250" in speaker.sent(1)


def test_speak_stream_stops_at_a_bad_sentence_once_text_is_out(manifest, con):
    speaker = Streamed(["Games lead with 105. ", "That is 62% of sales. ", "More."])
    chunks, spoken = stream_reply(speaker, manifest, con)
    assert chunks == ["Games lead with 105."]
    assert not spoken.verified


def test_speak_stream_falls_back_when_no_reply_checks_out(manifest, con):
    speaker = Streamed(*[["Games made 999."]] * 3)
    chunks, spoken = stream_reply(speaker, manifest, con)
    assert chunks == ["Here is Sales by category."]
    assert not spoken.verified


def test_turns_build_up_a_workspace_and_edit_charts_in_place(manifest, con):
    workspace, history = Workspace(), list[Exchange]()
    model = scripted(
        {"actions": [create()]},
        "Games lead at 105.",
        {
            "actions": [
                {"op": "update_chart", "chart_id": "c1", "spec": {"kind": "table"}}
            ]
        },
        {
            "actions": [
                create(
                    title="Orders",
                    sql="SELECT count(*) AS orders FROM orders",
                    spec={"kind": "stat", "value": "orders", "label": "orders"},
                )
            ]
        },
        "There are 6 orders.",
        {
            "actions": [
                {"op": "update_chart", "chart_id": "c1", "title": "Category sales"},
                {"op": "show_chart", "chart_id": "c2"},
            ]
        },
        "Renamed, and here are the 6 orders again.",
    )
    for question in (
        "sales by category?",
        "make that a table",
        "how many orders?",
        "rename the first, show orders",
    ):
        turn = take_turn(model, question, manifest, con, workspace, history)
    # Drawing c1 as a table was a view, not a revision; renaming it was one.
    assert [(c.id, c.title, c.spec.kind, c.revision) for c in workspace.charts] == [
        ("c1", "Category sales", "bar", 2),
        ("c2", "Orders", "stat", 1),
    ]
    assert history[1].reply == "Here's Sales by category as a table."
    assert workspace.charts[0].sql == TOTALS  # untouched fields are kept
    assert [e.reply for e in history][-1] == "Renamed, and here are the 6 orders again."
    assert [run.op for run in turn.runs] == ["update_chart", "show_chart"]
    # Later turns are told about the charts and the conversation so far.
    prompt = model.sent(5)
    assert "c1: 'Sales by category'" in prompt and "User: how many orders?" in prompt


def test_a_separate_speaker_writes_the_reply(manifest, con):
    planner = scripted({"actions": [create()]})
    speaker = scripted("Games lead with 105.")
    turn = take_turn(planner, "sales?", manifest, con, Workspace(), [], speaker=speaker)
    assert (turn.reply, len(planner.seen), len(speaker.seen)) == (
        "Games lead with 105.",
        1,
        1,
    )


def test_two_charts_created_in_one_turn_get_distinct_ids(manifest, con):
    second = create(
        title="Orders",
        sql="SELECT count(*) AS orders FROM orders",
        spec={"kind": "table"},
    )
    model = scripted({"actions": [create(), second]})
    planned = llm.plan(model, "both", manifest, con, Workspace(), [])
    assert [run.chart.id for run in planned.runs] == ["c1", "c2"]


def test_duplicate_titles_are_sent_back(manifest, con):
    model = scripted({"actions": [create(), create()]}, {"actions": [create()]})
    llm.plan(model, "twice", manifest, con, Workspace(), [])
    assert "already titled 'Sales by category'" in model.sent(1)


def test_set_view_redraws_without_a_reply_call_and_a_new_definition_resets_it(
    manifest, con
):
    workspace, history = Workspace(), list[Exchange]()
    model = scripted(
        {"actions": [create()]},
        "Games lead at 105.",
        {"actions": [{"op": "set_view", "chart_id": "c1", "kind": "donut"}]},
        {"actions": [{"op": "update_chart", "chart_id": "c1", "title": "By category"}]},
        "Renamed.",
    )
    take_turn(model, "sales by category?", manifest, con, workspace, history)
    turn = take_turn(model, "make that a donut", manifest, con, workspace, history)
    assert [run.op for run in turn.runs] == ["set_view"]
    assert turn.reply == "Here's Sales by category as a donut chart."
    assert (workspace.charts[0].view, workspace.charts[0].revision) == ("donut", 1)
    take_turn(model, "rename it", manifest, con, workspace, history)
    assert (workspace.charts[0].view, workspace.charts[0].revision) == (None, 2)


def test_set_view_to_a_kind_the_result_does_not_fit_is_sent_back(manifest, con):
    workspace, history = Workspace(), list[Exchange]()
    model = scripted(
        {"actions": [create()]},
        "Games lead at 105.",
        {"actions": [{"op": "set_view", "chart_id": "c1", "kind": "scatter3d"}]},
        {"actions": [{"op": "set_view", "chart_id": "c1", "kind": "table"}]},
    )
    take_turn(model, "sales by category?", manifest, con, workspace, history)
    take_turn(model, "show it in 3D", manifest, con, workspace, history)
    assert "cannot be drawn as 'scatter3d'" in model.sent(3)
    assert "do not draw it as another kind" in model.sent(3)
    assert workspace.charts[0].view == "table"
