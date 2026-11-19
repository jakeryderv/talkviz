"""What a chat runs on: its models, their reasoning effort, and the router.

Each chat keeps its own settings, copied from the defaults when it is made,
so chats run on different models can be compared. The defaults start from
the environment (TALKVIZ_MODEL and the rest) until the user saves their own.
API keys are not settings: they stay in the environment.
"""

import os
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from talkviz import llm
from talkviz.llm import Effort, Model


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    """The OpenRouter model that plans charts."""
    effort: Effort | None = None
    """How much it reasons first; None leaves that to the model."""
    reply_model: str | None = Field(None, min_length=1, max_length=200)
    """The model that writes replies; None means the planning model does."""
    reply_effort: Effort | None = None
    router: bool = True
    """Whether the fast router sees each message first."""


def from_environment() -> Settings:
    """The settings the environment variables and built-in defaults give."""
    get = os.environ.get
    model, effort = get("TALKVIZ_MODEL"), get("TALKVIZ_EFFORT")
    reply, reply_effort = get("TALKVIZ_SPEECH_MODEL"), get("TALKVIZ_SPEECH_EFFORT")
    # The default efforts were tuned for the default models, so each applies
    # only to its own; a model the user names keeps its own default.
    if not model:
        model, effort = llm.DEFAULT_MODEL, effort or llm.DEFAULT_EFFORT
    if not reply and llm.DEFAULT_SPEECH_MODEL:
        reply = llm.DEFAULT_SPEECH_MODEL
        reply_effort = reply_effort or llm.DEFAULT_SPEECH_EFFORT
    return Settings.model_validate(
        {
            "model": model,
            "effort": effort or None,
            "reply_model": reply or None,
            "reply_effort": reply_effort or None,
            "router": get("TALKVIZ_ROUTER") != "off",
        }
    )


def models(settings: Settings) -> tuple[Model, Model | None]:
    """The planning model and, if it is a different one, the reply model."""
    planner = _model(settings.model, settings.effort)
    if settings.reply_model is None:
        return planner, None
    return planner, _model(settings.reply_model, settings.reply_effort)


@lru_cache(maxsize=32)
def _model(name: str, effort: str | None) -> Model:
    return llm.exact(name, effort)
