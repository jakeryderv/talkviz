"""Speech in and out, through ElevenLabs: transcribing what the user said,
and reading replies aloud. Needs ELEVENLABS_API_KEY in the environment.

The browser never talks to ElevenLabs or sees the key: audio goes through
the talkviz server.
"""

import os
from collections.abc import Iterator

import httpx
from pydantic import BaseModel, ConfigDict, Field

API = "https://api.elevenlabs.io"
DEFAULT_LISTEN_MODEL = "scribe_v2"
# The fastest model to start speaking; replies are short, so it matters more
# than the last bit of expressiveness.
DEFAULT_SPEAK_MODEL = "eleven_flash_v2_5"
# A voice every account has, used until one is chosen.
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"
AUDIO_FORMAT = "mp3_44100_128"
MAX_AUDIO_BYTES = 10_000_000  # a spoken question, not a recording
TIMEOUT = 30.0


class VoiceSettings(BaseModel):
    """How a chat listens and speaks."""

    model_config = ConfigDict(extra="forbid")

    listen_model: str = Field(DEFAULT_LISTEN_MODEL, min_length=1, max_length=100)
    language: str | None = Field(None, min_length=2, max_length=3)
    """ISO 639 code of the language spoken; None lets the model detect it."""
    speak: bool = True
    """Whether replies are read aloud in voice mode."""
    voice_id: str | None = Field(None, min_length=1, max_length=100)
    """The ElevenLabs voice; None uses DEFAULT_VOICE."""
    speak_model: str = Field(DEFAULT_SPEAK_MODEL, min_length=1, max_length=100)
    speed: float = Field(1.0, ge=0.7, le=1.2)


class Voice(BaseModel):
    id: str
    name: str
    description: str = ""
    """A few words on how it sounds: "american, female, calm"."""
    preview_url: str | None = None


# Voices every account has, offered when the API key may not list voices.
PREMADE = [
    Voice(id="JBFqnCBsd6RMkjVDRZzb", name="George", description="british, male"),
    Voice(id="EXAVITQu4vr4xnSDxMaL", name="Sarah", description="american, female"),
    Voice(id="21m00Tcm4TlvDq8ikWAM", name="Rachel", description="american, female"),
]
SAMPLE = "This is how replies will sound."


class VoiceUnavailable(Exception):
    """Speech could not be used; the message says why, for the user."""


def available() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def transcribe(
    audio: bytes,
    content_type: str,
    settings: VoiceSettings,
    keyterms: list[str] | None = None,
    client: httpx.Client | None = None,
) -> str:
    """The words in a recording. `keyterms` are words likely to be said that
    a transcriber could otherwise mishear, such as names in the data."""
    fields: dict[str, str | list[str]] = {
        "model_id": settings.listen_model,
        "tag_audio_events": "false",
    }
    if settings.language:
        fields["language_code"] = settings.language
    if keyterms:
        fields["keyterms"] = keyterms[:MAX_KEYTERMS]
    response = _send(
        client,
        "POST",
        "/v1/speech-to-text",
        data=fields,
        files={"file": ("speech", audio, content_type or "audio/webm")},
    )
    return str(response.json().get("text", "")).strip()


MAX_KEYTERMS = 100


def speak(
    text: str, settings: VoiceSettings, client: httpx.Client | None = None
) -> Iterator[bytes]:
    """`text` read aloud, as MP3 audio in pieces as it is made."""
    voice = settings.voice_id or DEFAULT_VOICE
    body = {
        "text": text,
        "model_id": settings.speak_model,
        "voice_settings": {"speed": settings.speed},
    }
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        with client.stream(
            "POST",
            f"{API}/v1/text-to-speech/{voice}/stream",
            params={"output_format": AUDIO_FORMAT},
            headers=_headers(),
            json=body,
        ) as response:
            if response.is_error:
                response.read()
                raise VoiceUnavailable(_reason(response))
            yield from response.iter_bytes()
    except httpx.HTTPError as error:
        raise VoiceUnavailable(f"ElevenLabs could not be reached: {error}") from error
    finally:
        if own:
            client.close()


def voices(client: httpx.Client | None = None) -> list[Voice]:
    """The voices the account can use."""
    found = _send(client, "GET", "/v2/voices", params={"page_size": 100}).json()
    return [
        Voice(
            id=voice["voice_id"],
            name=voice.get("name") or voice["voice_id"],
            description=", ".join(
                str(value)
                for key in ("accent", "gender", "descriptive")
                if (value := (voice.get("labels") or {}).get(key))
            ),
            preview_url=voice.get("preview_url"),
        )
        for voice in found.get("voices", [])
    ]


def _headers() -> dict[str, str]:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceUnavailable("ELEVENLABS_API_KEY is not set.")
    return {"xi-api-key": key}


def _send(
    client: httpx.Client | None, method: str, path: str, **request
) -> httpx.Response:
    headers = _headers()
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT) as own:
                response = own.request(method, API + path, headers=headers, **request)
        else:
            response = client.request(method, API + path, headers=headers, **request)
    except httpx.HTTPError as error:
        raise VoiceUnavailable(f"ElevenLabs could not be reached: {error}") from error
    if response.is_error:
        raise VoiceUnavailable(_reason(response))
    return response


def _reason(response: httpx.Response) -> str:
    """ElevenLabs's own account of an error, without anything of ours in it."""
    try:
        detail = response.json().get("detail")
        message = detail.get("message") if isinstance(detail, dict) else detail
    except (ValueError, AttributeError):
        message = None
    return f"ElevenLabs refused the request ({response.status_code}): {message or response.reason_phrase}"
