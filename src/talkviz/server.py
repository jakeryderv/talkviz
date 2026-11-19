"""The local web app: a JSON API over the turn pipeline and the store, plus the
built interface served from talkviz/web.
"""

import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from talkviz import llm, route, settings
from talkviz.charts import ChartSpec, views
from talkviz.llm import Exchange, Model
from talkviz.settings import Settings
from talkviz.sources import Source, list_sources, load_source
from talkviz.sql import QueryRejected, QueryResult, ResultColumn
from talkviz.store import Chat, Event, Run, Store
from talkviz.turn import Turn, take_turn
from talkviz.workspace import Chart, Workspace, run_chart

WEB = Path(__file__).parent / "web"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
CATALOG_SECONDS = 3600
TITLE_LENGTH = 60
MAX_SHOWN = 4  # charts side by side


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
    views: list[ChartSpec]
    """Every way the result can be drawn, the chart's own spec first."""


class ModelInfo(BaseModel):
    id: str
    name: str


class SettingsView(BaseModel):
    defaults: Settings
    """What a new chat starts with."""
    efforts: list[str]
    keys: dict[str, bool]
    """Whether each service's API key is set. Keys themselves stay in the
    environment and are never sent to the browser."""


class ChatView(BaseModel):
    chat: Chat
    settings: Settings
    charts: list[ChartView]
    events: list[Event]
    shown: list[str]
    """Ids of the charts to show: those the latest turn created, changed, or
    showed. More than one is drawn side by side."""


class NewChat(BaseModel):
    source: str | None = None
    """Needed only when more than one source is registered."""


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class View(BaseModel):
    kind: str


class Say(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TurnView(BaseModel):
    """What one turn added to the chat."""

    chat: Chat
    events: list[Event]
    charts: list[ChartView]
    """The charts the turn created, changed, or showed."""
    shown: list[str]


def create_app(model: Model | None = None, store: Store | None = None) -> FastAPI:
    """The app. `model` defaults to the configured OpenRouter model, created
    on the first turn so the app starts without an API key."""
    app = FastAPI(title="talkviz")
    app.state.model = model
    app.state.speaker = None  # a separate model for replies, if one is configured
    app.state.router = None  # a fast first look at each message, if configured
    app.state.store = store or Store()

    def get_store() -> Store:
        return app.state.store

    app.state.jev = None  # the router, made on first use
    app.state.catalog = (0.0, [])  # when OpenRouter's models were listed, and they

    def defaults() -> Settings:
        return app.state.store.default_settings() or settings.from_environment()

    def settings_of(chat: Chat) -> Settings:
        return app.state.store.chat_settings(chat.id) or defaults()

    def engines(chosen: Settings) -> tuple[Model, Model | None, route.Router | None]:
        """The planner, reply model and router a turn runs on. A model given
        to create_app() stands in for all the chat's settings name."""
        if app.state.model is not None:
            return app.state.model, app.state.speaker, app.state.router
        try:
            planner, speaker = settings.models(chosen)
            if chosen.router and app.state.jev is None:
                app.state.jev = route.jev()
        except Exception as error:  # whatever the provider raises
            raise HTTPException(503, f"No model available: {error}") from error
        return planner, speaker, app.state.jev if chosen.router else None

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
        return store.create_chat(source.name, defaults())

    @app.get("/api/settings")
    def get_settings() -> SettingsView:
        return SettingsView(
            defaults=defaults(),
            efforts=list(llm.EFFORTS),
            keys={
                "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
                "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
            },
        )

    @app.put("/api/settings")
    def save_defaults(body: Settings, store: Stored) -> Settings:
        """Set what new chats start with. Existing chats keep their own."""
        store.set_default_settings(body)
        return body

    @app.put("/api/chats/{chat_id}/settings")
    def save_settings(body: Settings, chat: Current, store: Stored) -> Settings:
        store.set_chat_settings(chat.id, body)
        return body

    @app.get("/api/models")
    def models() -> list[ModelInfo]:
        """OpenRouter's models, to pick from. Empty if they cannot be listed;
        a model id can still be typed in."""
        listed_at, listed = app.state.catalog
        if listed and time.time() - listed_at < CATALOG_SECONDS:
            return listed
        try:
            response = httpx.get(CATALOG_URL, timeout=10)
            response.raise_for_status()
            listed = sorted(
                (
                    ModelInfo(id=m["id"], name=m.get("name") or m["id"])
                    for m in response.json()["data"]
                ),
                key=lambda m: m.id,
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return listed
        app.state.catalog = (time.time(), listed)
        return listed

    @app.get("/api/chats/{chat_id}")
    def chat(chat: Current, store: Stored) -> ChatView:
        events = store.events(chat.id)
        return ChatView(
            chat=chat,
            settings=settings_of(chat),
            charts=_views(store, chat.id, store.charts(chat.id)),
            events=events,
            shown=_shown(events),
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
    ) -> TurnView:
        chosen = settings_of(chat)
        model, speaker, router = engines(chosen)
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
                    speaker=speaker,
                    router=router,
                    focus=(_shown(history) or [None])[-1],
                    seen=_seen(_views(store, chat.id, workspace.charts)),
                )
        except llm.ModelFailed as error:
            reply = error.reply()
            events.append(
                store.add_event(
                    chat.id, "assistant", text=reply, verified=False, error=str(error)
                )
            )
            return TurnView(chat=chat, events=events, charts=[], shown=[])
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
                    view=run.chart.view if run.op == "set_view" else None,
                )
            )
        # Sub-charts that keep rows of a chart this turn changed follow it.
        changed = {run.chart.id for run in taken.runs if run.op == "update_chart"}
        for child in workspace.charts:
            if child.narrow and child.parent in changed and child not in touched:
                child = child.model_copy(update={"sql": workspace.source(child)})
                try:
                    with source.connect() as con:
                        result = run_chart(con, source.manifest, workspace, child)
                except (QueryRejected, ValueError):
                    continue  # it no longer applies; it says so when opened
                store.save_chart(chat.id, child)
                store.save_run(chat.id, child, result, version)
                touched.append(child)
        events.append(
            store.add_event(
                chat.id,
                "assistant",
                text=taken.reply,
                verified=taken.reply_verified,
                rejected=[asdict(draft) for draft in taken.rejected] or None,
                models=_models(chosen, taken, router),
                seconds={k: round(v, 2) for k, v in taken.seconds.items()},
            )
        )
        return TurnView(
            chat=chat,
            events=events,
            charts=_views(store, chat.id, touched),
            shown=_shown(events),
        )

    @app.post("/api/chats/{chat_id}/charts/{chart_id}/run")
    def rerun(chart_id: str, chat: Current, store: Stored) -> ChartView:
        """Run a chart again on the current data. Says nothing in the feed."""
        workspace = Workspace(charts=store.charts(chat.id))
        chart = workspace.get(chart_id)
        if chart is None:
            raise HTTPException(404, "No such chart.")
        source = _source(chat)
        try:
            with source.connect() as con:
                result = run_chart(con, source.manifest, workspace, chart)
                version = source.data_version(con)
        except (QueryRejected, ValueError) as error:
            # The data or its manifest changed under a saved chart.
            raise HTTPException(409, f"This chart no longer runs: {error}") from error
        run = store.save_run(chat.id, chart, result, version)
        return _view(chart, run)

    @app.delete("/api/chats/{chat_id}/charts/{chart_id}/lens", status_code=204)
    def reset_lens(chart_id: str, chat: Current, store: Stored) -> None:
        """Put the whole chart back in view. Not a revision."""
        store.set_lens(chat.id, chart_id, None)

    @app.put("/api/chats/{chat_id}/charts/{chart_id}/view")
    def set_view(chart_id: str, body: View, chat: Current, store: Stored) -> ChartView:
        """Draw a chart's latest result another way. No query, revision, or
        feed entry: the chart itself is unchanged."""
        chart = next((c for c in store.charts(chat.id) if c.id == chart_id), None)
        if chart is None:
            raise HTTPException(404, "No such chart.")
        shown = _view(chart, store.latest_runs(chat.id).get(chart_id))
        if body.kind not in [view.kind for view in shown.views]:
            raise HTTPException(400, f"This chart cannot be drawn as {body.kind!r}.")
        # The spec's own kind is the default, stored as no view.
        view = None if body.kind == chart.spec.kind else body.kind
        store.set_view(chat.id, chart_id, view)
        return shown.model_copy(update={"view": view})

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
    return [_view(chart, runs.get(chart.id)) for chart in charts]


def _view(chart: Chart, run: Run | None) -> ChartView:
    if run is None:
        return ChartView(**chart.model_dump(), run=None, views=[chart.spec])
    result = QueryResult(
        columns=[ResultColumn(c.name, c.type) for c in run.columns],
        rows=[tuple(row) for row in run.rows],
        truncated=run.truncated,
        seconds=run.seconds,
    )
    return ChartView(**chart.model_dump(), run=run, views=views(chart.spec, result))


def _seen(charts: list[ChartView]) -> dict[str, route.Seen]:
    return {
        chart.id: route.Seen(
            columns=[column.name for column in chart.run.columns],
            views=[view.kind for view in chart.views[1:]],
        )
        for chart in charts
        if chart.run is not None
    }


def _shown(events: list[Event]) -> list[str]:
    """The charts touched by the latest turn that touched any, oldest first."""
    last = next(
        (i for i in reversed(range(len(events))) if events[i].kind == "chart"), None
    )
    if last is None:
        return []
    found: list[str] = []
    for event in reversed(events[: last + 1]):
        if event.kind != "chart":
            break
        if event.chart_id and event.chart_id not in found:
            found.insert(0, event.chart_id)
    return found[-MAX_SHOWN:]


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


def _models(chosen: Settings, taken: Turn, router: route.Router | None) -> dict:
    """The model behind each stage of a turn, leaving out stages that ran
    without one: a shortcut has no plan, and a fixed sentence no reply."""
    used = {}
    if router is not None and (taken.shortcut or taken.seconds["route"] >= 0.01):
        used["route"] = getattr(router, "model", "router")
    if not taken.shortcut:
        used["plan"] = chosen.model
    if llm.view_reply(taken.runs) is None:
        used["reply"] = chosen.reply_model or chosen.model
    return used
