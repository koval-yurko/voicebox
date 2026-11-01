"""
Tests for POST /tts/render and main -> TTS worker forwarding (--role main --tts-url).
"""

import asyncio
import functools

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import config, roles
from backend.database import get_db
from backend.routes import tts_stream
from backend.services import generation, workers
from backend.utils import cache

AUDIO = np.array([0.0, 0.25, -0.5, 1.0], dtype=np.float32)
PARAMS = dict(
    profile_id="p",
    text="hello",
    language="en",
    engine="qwen",
    model_size="0.6B",
    seed=None,
    instruct=None,
    max_chunk_chars=None,
    crossfade_ms=None,
)


@pytest.fixture
def render_app(monkeypatch):
    calls = []

    async def fake_synthesize(**kwargs):
        calls.append(kwargs)
        if kwargs["text"] == "boom":
            raise ValueError("Profile not found")
        return AUDIO, 24000

    monkeypatch.setattr(generation, "synthesize", fake_synthesize)
    app = FastAPI()
    app.include_router(tts_stream.router)
    app.dependency_overrides[get_db] = lambda: None
    app.state.calls = calls
    return app


def test_render_returns_float32_pcm(render_app):
    with TestClient(render_app) as client:
        response = client.post("/tts/render", json=PARAMS)
    assert response.status_code == 200
    assert response.headers["x-sample-rate"] == "24000"
    assert np.array_equal(np.frombuffer(response.content, dtype=np.float32), AUDIO)
    assert render_app.state.calls[0]["model_size"] == "0.6B"
    assert not tts_stream.inference_lock.locked()


def test_render_error_has_detail(render_app):
    with TestClient(render_app) as client:
        response = client.post("/tts/render", json={**PARAMS, "text": "boom"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Profile not found"
    assert not tts_stream.inference_lock.locked()


def test_stoppable_refuses_next_chunk():
    class Backend:
        calls = 0

        async def generate(self, *args):
            Backend.calls += 1
            return AUDIO, 24000

    with pytest.raises(generation.RenderStopped):
        asyncio.run(generation._Stoppable(Backend(), lambda: True).generate("t", {}, "en", None, None))
    assert Backend.calls == 0
    assert asyncio.run(generation._Stoppable(Backend(), lambda: False).generate("t", {}, "en", None, None))[1] == 24000


def test_render_speech_goes_to_worker(monkeypatch, render_app):
    client = functools.partial(httpx.AsyncClient, transport=httpx.ASGITransport(app=render_app))
    monkeypatch.setattr(workers.httpx, "AsyncClient", client)
    monkeypatch.setenv("VOICEBOX_ROLE", "main")
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://tts-worker")
    statuses = []

    async def on_status(s):
        statuses.append(s)

    audio, sr = asyncio.run(generation.render_speech(db=None, on_status=on_status, **PARAMS))
    assert np.array_equal(audio, AUDIO) and sr == 24000
    assert audio.flags.writeable
    assert statuses == ["generating"]
    assert len(render_app.state.calls) == 1  # 1 render, done by the worker app


def test_render_speech_worker_down(monkeypatch):
    monkeypatch.setenv("VOICEBOX_ROLE", "main")
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://127.0.0.1:1")
    with pytest.raises(workers.WorkerError, match="not reachable"):
        asyncio.run(generation.render_speech(db=None, **PARAMS))


@pytest.mark.parametrize("role", ["all", "tts"])
def test_tts_url_ignored_outside_main(monkeypatch, role):
    monkeypatch.setenv("VOICEBOX_ROLE", role)
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://tts-worker")
    assert roles.tts_url() is None


def test_voice_prompt_cache_write_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_data_dir", tmp_path)
    monkeypatch.setattr(cache, "_memory_cache", {})
    cache.cache_voice_prompt("k", {"a": 1})
    files = sorted(p.name for p in cache._get_cache_dir().iterdir())
    assert files == ["k.prompt"]
    monkeypatch.setattr(cache, "_memory_cache", {})
    assert cache.get_cached_voice_prompt("k") == {"a": 1}
