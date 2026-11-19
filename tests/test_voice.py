"""Speech in and out, with ElevenLabs stood in for."""

import json

import httpx
import pytest
from test_llm import create
from test_server import client

from talkviz import voice
from talkviz.voice import VoiceSettings


def service(respond) -> tuple[httpx.Client, list[httpx.Request]]:
    """A client whose requests go to `respond`, and the requests it was sent."""
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        request.read()
        sent.append(request)
        return respond(request)

    return httpx.Client(transport=httpx.MockTransport(handle)), sent


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "secret")


def test_a_recording_is_sent_for_its_words(key):
    http, sent = service(lambda r: httpx.Response(200, json={"text": " hide Ford "}))
    chosen = VoiceSettings(language="en")
    assert voice.transcribe(b"sound", "audio/webm", chosen, client=http) == "hide Ford"
    [request] = sent
    assert request.url.path == "/v1/speech-to-text"
    assert request.headers["xi-api-key"] == "secret"
    body = request.content
    assert b"scribe_v2" in body and b"sound" in body and b"language_code" in body


def test_a_reply_is_spoken_in_the_chosen_voice_as_it_arrives(key):
    http, sent = service(lambda r: httpx.Response(200, content=b"mp3 bytes"))
    chosen = VoiceSettings(voice_id="v9", speed=1.1)
    assert b"".join(voice.speak("Kia leads.", chosen, client=http)) == b"mp3 bytes"
    [request] = sent
    assert request.url.path == "/v1/text-to-speech/v9/stream"
    assert json.loads(request.content) == {
        "text": "Kia leads.",
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {"speed": 1.1},
    }
    # With no voice chosen, one every account has is used.
    b"".join(voice.speak("Hi.", VoiceSettings(), client=http))
    assert sent[-1].url.path == f"/v1/text-to-speech/{voice.DEFAULT_VOICE}/stream"


def test_a_refusal_says_why_in_the_services_words(key):
    refusal = {"detail": {"message": "A voice with voice_id 'v9' was not found."}}
    http, _ = service(lambda r: httpx.Response(404, json=refusal))
    with pytest.raises(voice.VoiceUnavailable, match="'v9' was not found"):
        b"".join(voice.speak("Hi.", VoiceSettings(voice_id="v9"), client=http))


def test_without_a_key_speech_is_unavailable(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(voice.VoiceUnavailable, match="ELEVENLABS_API_KEY is not set"):
        voice.transcribe(b"sound", "audio/webm", VoiceSettings())


def test_the_speed_must_be_one_the_service_takes():
    with pytest.raises(ValueError):
        VoiceSettings(speed=2.0)


def test_the_app_transcribes_and_reads_replies_aloud(source, key, monkeypatch):
    heard = []

    def transcribe(audio, kind, chosen, keyterms=None, client=None):
        heard.append((audio, kind, chosen.language))
        return "sales by category?"

    monkeypatch.setattr(voice, "transcribe", transcribe)
    monkeypatch.setattr(
        voice,
        "speak",
        lambda text, chosen, client=None: iter([b"<", text.encode(), b">"]),
    )
    app = client({"actions": [create()]}, "Games lead at 105.")
    chat_id = app.post("/api/chats", json={}).json()["id"]
    spoken = app.post(
        f"/api/transcribe?chat={chat_id}",
        content=b"sound",
        headers={"content-type": "audio/webm;codecs=opus"},
    )
    assert spoken.json() == {"text": "sales by category?"}
    assert heard == [(b"sound", "audio/webm;codecs=opus", None)]
    assert app.post("/api/transcribe", content=b"").status_code == 400

    turn = app.post(f"/api/chats/{chat_id}/turns", json={"text": "sales?"}).json()
    user, *_, reply = turn["events"]
    audio = app.get(f"/api/chats/{chat_id}/events/{reply['id']}/audio")
    assert audio.headers["content-type"] == "audio/mpeg"
    assert audio.content == b"<Games lead at 105.>"
    # Only replies are read aloud.
    assert app.get(f"/api/chats/{chat_id}/events/{user['id']}/audio").status_code == 404


def test_a_speech_failure_is_an_error_response_that_says_why(source, key, monkeypatch):
    def refuse(text, chosen, client=None):
        raise voice.VoiceUnavailable("ElevenLabs refused the request (401): no credit")
        yield b""

    monkeypatch.setattr(voice, "speak", refuse)
    failed = client().get("/api/voices/v9/sample")
    assert (failed.status_code, failed.json()["detail"]) == (
        502,
        "ElevenLabs refused the request (401): no credit",
    )


def test_voices_fall_back_to_ones_every_account_has(source, key, monkeypatch):
    def refuse(client=None):
        raise voice.VoiceUnavailable("missing the permission voices_read")

    monkeypatch.setattr(voice, "voices", refuse)
    listed = client().get("/api/voices").json()
    assert listed["note"] == "missing the permission voices_read"
    assert [v["name"] for v in listed["voices"]] == ["George", "Sarah", "Rachel"]
