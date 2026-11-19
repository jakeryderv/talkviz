"""The local web app: a JSON API over the turn pipeline and the store, plus the
built interface served from talkviz/web.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from talkviz import llm
from talkviz.llm import Exchange, Model
from talkviz.sources import Source, list_sources, load_source
from talkviz.sql import QueryRejected, check_and_run
from talkviz.store import Chat, Event, Run, Store
from talkviz.turn import take_turn
from talkviz.workspace import Chart, Workspace

WEB = Path(__file__).parent / "web"
TITLE_LENGTH = 60


class SourceInfo(BaseModel):
    name: str
    title: str
    description: str
    data_version: str | None
    """None if the database could not be opened."""
    examples: list[str]
    """Questions from the manifest, to suggest in a new chat."""


class ChartView(Chart):
    """A chart with its most recent result."""

    run: Run | None


class ChatView(BaseModel):
    chat: Chat
    charts: list[ChartView]
    events: list[Event]
    focus: str | None
    """Id of the chart to show: the one most recently created, changed, or shown."""


class NewChat(BaseModel):
    source: str | None = None
    """Needed only when more than one source is registered."""


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class Say(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TurnView(BaseModel):
    """What one turn added to the chat."""

    chat: Chat
    events: list[Event]
    charts: list[ChartView]
    """The charts the turn created, changed, or showed."""
    focus: str | None


def create_app(model: Model | None = None, store: Store | None = None) -> FastAPI:
    """The app. `model` defaults to the configured OpenRouter model, created
    on the first turn so the app starts without an API key."""
    app = FastAPI(title="talkviz")
    app.state.model = model
    app.state.speaker = None  # a separate model for replies, if one is configured
    app.state.store = store or Store()

    def get_store() -> Store:
        return app.state.store

    def get_model() -> Model:
        if app.state.model is None:
            try:
                app.state.model = llm.openrouter()
                app.state.speaker = llm.speech_openrouter()
            except Exception as error:  # whatever the provider raises
                raise HTTPException(503, f"No model available: {error}") from error
        return app.state.model

    def get_chat(chat_id: str, store: Annotated[Store, Depends(get_store)]) -> Chat:
        chat = store.chat(chat_id)
        if chat is None:
            raise HTTPException(404, "No such chat.")
        return chat

    Stored = Annotated[Store, Depends(get_store)]
    Current = Annotated[Chat, Depends(get_chat)]

    @app.get("/api/sources")
    def sources() -> list[SourceInfo]:
        return [_source_info(load_source(name)) for name in list_sources()]

    @app.get("/api/chats")
    def chats(store: Stored) -> list[Chat]:
        return store.chats()

    @app.post("/api/chats")
    def new_chat(body: NewChat, store: Stored) -> Chat:
        try:
            source = load_source(body.source)
        except LookupError as error:
            raise HTTPException(400, str(error)) from error
        return store.create_chat(source.name)

    @app.get("/api/chats/{chat_id}")
    def chat(chat: Current, store: Stored) -> ChatView:
        events = store.events(chat.id)
        return ChatView(
            chat=chat,
            charts=_views(store, chat.id, store.charts(chat.id)),
            events=events,
            focus=_focus(events),
        )

    @app.patch("/api/chats/{chat_id}")
    def rename(body: Rename, chat: Current, store: Stored) -> Chat:
        store.rename_chat(chat.id, body.title)
        return chat.model_copy(update={"title": body.title})

    @app.delete("/api/chats/{chat_id}", status_code=204)
    def delete(chat: Current, store: Stored) -> None:
        store.delete_chat(chat.id)

    @app.post("/api/chats/{chat_id}/turns")
    def turn(
        body: Say,
        chat: Current,
        store: Stored,
        model: Annotated[Model, Depends(get_model)],
    ) -> TurnView:
        source = _source(chat)
        history = store.events(chat.id)
        workspace = Workspace(charts=store.charts(chat.id))
        events = [store.add_event(chat.id, "user", text=body.text)]
        if not history:
            chat = chat.model_copy(update={"title": _title(body.text)})
            store.rename_chat(chat.id, chat.title)
        touched: list[Chart] = []
        try:
            with source.connect() as con:
                version = source.data_version(con)
                taken = take_turn(
                    model,
                    body.text,
                    source.manifest,
                    con,
                    workspace,
                    _exchanges(history),
                    speaker=app.state.speaker,
                )
        except llm.ModelFailed as error:
            reply = "Sorry, I couldn't work that out. Try asking another way."
            events.append(
                store.add_event(
                    chat.id, "assistant", text=reply, verified=False, error=str(error)
                )
            )
            return TurnView(chat=chat, events=events, charts=[], focus=None)
        for run in taken.runs:
            store.save_chart(chat.id, run.chart)
            saved = store.save_run(chat.id, run.chart, run.result, version)
            touched.append(run.chart)
            events.append(
                store.add_event(
                    chat.id,
                    "chart",
                    op=run.op,
                    chart_id=run.chart.id,
                    revision=run.chart.revision,
                    title=run.chart.title,
                    run_id=saved.id,
                )
            )
        events.append(
            store.add_event(
                chat.id, "assistant", text=taken.reply, verified=taken.reply_verified
            )
        )
        return TurnView(
            chat=chat,
            events=events,
            charts=_views(store, chat.id, touched),
            focus=_focus(events),
        )

    @app.post("/api/chats/{chat_id}/charts/{chart_id}/run")
    def rerun(chart_id: str, chat: Current, store: Stored) -> ChartView:
        """Run a chart again on the current data. Says nothing in the feed."""
        chart = next((c for c in store.charts(chat.id) if c.id == chart_id), None)
        if chart is None:
            raise HTTPException(404, "No such chart.")
        source = _source(chat)
        try:
            with source.connect() as con:
                result = check_and_run(con, chart.sql, source.manifest)
                version = source.data_version(con)
        except QueryRejected as error:
            # The data or its manifest changed under a saved chart.
            raise HTTPException(409, f"This chart no longer runs: {error}") from error
        run = store.save_run(chat.id, chart, result, version)
        return ChartView(**chart.model_dump(), run=run)

    if WEB.is_dir():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def _source(chat: Chat) -> Source:
    try:
        return load_source(chat.source)
    except (LookupError, FileNotFoundError) as error:
        raise HTTPException(409, f"This chat's data source is gone: {error}") from error


def _source_info(source: Source) -> SourceInfo:
    try:
        with source.connect() as con:
            version = source.data_version(con)
    except Exception:  # noqa: BLE001 - a broken source should still be listed
        version = None
    manifest = source.manifest
    return SourceInfo(
        name=source.name,
        title=manifest.title,
        description=" ".join(manifest.description.split()),
        data_version=version,
        examples=[example.question for example in manifest.examples],
    )


def _views(store: Store, chat_id: str, charts: list[Chart]) -> list[ChartView]:
    runs = store.latest_runs(chat_id)
    return [ChartView(**chart.model_dump(), run=runs.get(chart.id)) for chart in charts]


def _focus(events: list[Event]) -> str | None:
    return next((e.chart_id for e in reversed(events) if e.kind == "chart"), None)


def _exchanges(events: list[Event]) -> list[Exchange]:
    """The feed as question-and-reply pairs, for the model's context."""
    exchanges, question = [], None
    for event in events:
        if event.kind == "user":
            question = event.text
        elif event.kind == "assistant" and question is not None:
            exchanges.append(Exchange(question, event.text or ""))
            question = None
    return exchanges


def _title(text: str) -> str:
    text = " ".join(text.split())
    return (
        text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"
    )
