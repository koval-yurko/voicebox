"""
Tests for WS /stt/ws (backend/routes/stt_stream.py) with fake STT backends.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import backends
from backend.routes import stt_stream
from backend.tests.test_stt_streaming import FakeSTTBackend

FIXTURE = Path(__file__).parent / "fixtures" / "speech_pause_speech_16k.wav"


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    monkeypatch.setattr(stt_stream, "_default_stt_model", lambda: "turbo")
    backends.reset_backends()
    whisper, qwen = FakeSTTBackend("turbo"), FakeSTTBackend("0.6B")
    backends._stt_backends["whisper"] = whisper
    backends._stt_backends["qwen_asr"] = qwen
    yield whisper, qwen
    backends.reset_backends()


@pytest.fixture
def client(fakes):
    app = FastAPI()
    app.include_router(stt_stream.router)
    with TestClient(app) as c:
        yield c


def _pcm16(seconds: float, sr: int = 16000) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes()


def _recv(ws) -> dict:
    return json.loads(ws.receive_text())


def _start(ws, **fields) -> dict:
    ws.send_text(json.dumps({"type": "start", "vad": False, **fields}))
    return _recv(ws)


@pytest.mark.parametrize(
    ("model", "engine", "model_name"),
    [
        ("whisper-turbo", "whisper", "whisper-turbo"),
        ("turbo", "whisper", "whisper-turbo"),
        ("qwen-asr-0.6B", "qwen_asr", "qwen-asr-0.6B"),
        ("qwen-asr-1.7B", "qwen_asr", "qwen-asr-1.7B"),
    ],
)
def test_start_frames_end_segment_final_end(client, fakes, model, engine, model_name):
    with client.websocket_connect("/stt/ws") as ws:
        ready = _start(ws, model=model, language="en")
        assert ready["type"] == "ready"
        assert ready["model"] == model_name
        assert ready["capabilities"] == {
            "interim_results": False,
            "revises_text": False,
            "native_streaming": False,
            "min_latency_ms": 300,
            "needs_vad": True,
        }

        pcm = _pcm16(1.0)
        for i in range(0, len(pcm), 640):  # 20 ms frames
            ws.send_bytes(pcm[i : i + 640])
        ws.send_text(json.dumps({"type": "end_segment"}))
        final = _recv(ws)
        assert final == {
            "type": "final",
            "segment_id": 1,
            "text": "fake text 1",
            "language": "en",
            "start_s": 0.0,
            "end_s": 1.0,
        }

        ws.send_text(json.dumps({"type": "end"}))
        assert _recv(ws) == {"type": "end"}

    backend = backends._stt_backends[engine]
    assert backend.calls == [(16000, "en", model_name.split("-")[-1])]


def test_start_without_model_uses_capture_settings_default(client):
    with client.websocket_connect("/stt/ws") as ws:
        assert _start(ws)["model"] == "whisper-turbo"


def test_end_finalizes_open_segment(client):
    with client.websocket_connect("/stt/ws") as ws:
        _start(ws, sample_rate=48000)
        ws.send_bytes(_pcm16(0.5, sr=48000))
        ws.send_text(json.dumps({"type": "end"}))
        final = _recv(ws)
        assert final["type"] == "final"
        assert final["end_s"] == pytest.approx(0.5)
        assert _recv(ws) == {"type": "end"}


def test_cancel_segment_gives_no_final(client, fakes):
    whisper, _ = fakes
    with client.websocket_connect("/stt/ws") as ws:
        _start(ws)
        ws.send_bytes(_pcm16(0.5))
        ws.send_text(json.dumps({"type": "cancel_segment"}))
        ws.send_text(json.dumps({"type": "end"}))
        assert _recv(ws) == {"type": "end"}
    assert whisper.calls == []


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({"type": "start", "model": "bogus"}, "Unknown STT model 'bogus'"),
        ({"type": "start", "sample_rate": 1000}, "invalid start message"),
        ({"type": "start", "language": "../x"}, "invalid start message"),
        ({"type": "end_segment"}, "send a start message first"),
        ({"type": "nope"}, "send a start message first"),
    ],
)
def test_bad_messages_give_error(client, message, error):
    with client.websocket_connect("/stt/ws") as ws:
        ws.send_text(json.dumps(message))
        reply = _recv(ws)
        assert reply["type"] == "error"
        assert error in reply["message"]
        # The connection stays usable.
        assert _start(ws)["type"] == "ready"


def test_audio_before_start_gives_error(client):
    with client.websocket_connect("/stt/ws") as ws:
        ws.send_bytes(_pcm16(0.1))
        assert _recv(ws) == {"type": "error", "message": "send a start message first"}


def test_invalid_json_and_unknown_type_give_error(client):
    with client.websocket_connect("/stt/ws") as ws:
        ws.send_text("{not json")
        assert _recv(ws) == {"type": "error", "message": "invalid JSON"}
        _start(ws)
        ws.send_text(json.dumps({"type": "nope"}))
        assert _recv(ws) == {"type": "error", "message": "unknown message type: nope"}


def test_model_not_downloaded_gives_error(client, fakes):
    whisper, _ = fakes
    whisper._is_model_cached = lambda size: False
    with client.websocket_connect("/stt/ws") as ws:
        reply = _start(ws, model="whisper-turbo")
        assert reply["type"] == "error"
        assert "not downloaded" in reply["message"]
    assert not whisper.is_loaded()


def test_server_vad_sends_speech_events_and_closes_segments(client, fakes):
    whisper, _ = fakes
    audio, _ = sf.read(FIXTURE, dtype="int16")
    pcm = audio.tobytes()
    with client.websocket_connect("/stt/ws") as ws:
        ready = _start(ws, vad=True)
        assert ready["vad"] is True
        for i in range(0, len(pcm), 640):
            ws.send_bytes(pcm[i : i + 640])
        ws.send_text(json.dumps({"type": "end"}))
        messages = []
        while (msg := _recv(ws))["type"] != "end":
            messages.append(msg)

    for seg_id in (1, 2):
        assert [m["type"] for m in messages if m.get("segment_id") == seg_id] == [
            "speech_start",
            "speech_end",
            "final",
        ]
    assert len(messages) == 6
    assert len(whisper.calls) == 2


def test_client_disconnect_drops_open_segment(client, fakes):
    whisper, _ = fakes
    with client.websocket_connect("/stt/ws") as ws:
        _start(ws)
        ws.send_bytes(_pcm16(0.5))
    # Closed without "end": nothing is transcribed for a client that left.
    with client.websocket_connect("/stt/ws") as ws:
        _start(ws)
    assert whisper.calls == []
