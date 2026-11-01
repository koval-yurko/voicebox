"""
Tests for main -> STT worker forwarding (--role main --stt-url, backend/services/workers.py).
"""

import asyncio
import functools

import httpx
import pytest
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from backend import backends
from backend.services import transcribe, workers

URL = "http://stt-worker"


def _fake_worker():
    app = FastAPI()
    app.state.calls = []

    @app.post("/transcribe")
    async def transcribe_route(file: UploadFile = File(...), model: str = Form(...), language: str | None = Form(None)):
        body = await file.read()
        app.state.calls.append(("transcribe", model, language, len(body)))
        if model == "whisper-base":
            raise HTTPException(status_code=202, detail={"message": "Whisper Base is being downloaded"})
        if model == "whisper-small":
            raise HTTPException(status_code=500, detail="engine exploded")
        return {"text": f"hello from {model}", "duration": 1.0}

    @app.post("/models/load")
    async def load(body: dict):
        app.state.calls.append(("load", body["model_name"]))
        return {"message": "ok"}

    @app.post("/models/{name}/unload")
    async def unload(name: str):
        return {"message": f"Model {name} unloaded successfully"}

    @app.get("/models/status")
    async def status():
        return {"models": [{"model_name": "qwen-asr-0.6B", "loaded": True}, {"model_name": "whisper-turbo", "loaded": False}]}

    return app


@pytest.fixture
def worker(monkeypatch):
    app = _fake_worker()
    client = functools.partial(httpx.AsyncClient, transport=httpx.ASGITransport(app=app))
    monkeypatch.setattr(workers.httpx, "AsyncClient", client)
    monkeypatch.setenv("VOICEBOX_ROLE", "main")
    monkeypatch.setenv("VOICEBOX_STT_URL", URL + "/")

    def no_local_stt(*_a, **_k):
        raise AssertionError("main must not use a local STT backend")

    monkeypatch.setattr(transcribe, "get_stt_backend", no_local_stt)
    monkeypatch.setattr(transcribe, "unload_other_stt_engines", no_local_stt)
    return app


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF" + b"\0" * 100)
    return str(path)


def test_transcribe_goes_to_worker(worker, wav):
    config = backends.resolve_stt_model("qwen-asr-0.6B")
    text = asyncio.run(transcribe.transcribe_with_model(config, wav, "en"))
    assert text == "hello from qwen-asr-0.6B"
    assert worker.state.calls == [("transcribe", "qwen-asr-0.6B", "en", 104)]


def test_load_goes_to_worker(worker):
    asyncio.run(transcribe.load_stt_model(backends.resolve_stt_model("qwen-asr-0.6B")))
    assert worker.state.calls == [("load", "qwen-asr-0.6B")]


@pytest.mark.parametrize("model, message", [("whisper-base", "202"), ("whisper-small", "engine exploded")])
def test_worker_errors_raise(worker, wav, model, message):
    with pytest.raises(workers.WorkerError, match=message):
        asyncio.run(transcribe.transcribe_with_model(backends.resolve_stt_model(model), wav, None))


def test_worker_down_raises(monkeypatch, wav):
    monkeypatch.setenv("VOICEBOX_ROLE", "main")
    monkeypatch.setenv("VOICEBOX_STT_URL", "http://127.0.0.1:1")
    with pytest.raises(workers.WorkerError, match="not reachable"):
        asyncio.run(transcribe.transcribe_with_model(backends.resolve_stt_model("qwen-asr-0.6B"), wav, None))


def test_status_and_unload(worker):
    assert asyncio.run(workers.loaded_models(URL)) == {"qwen-asr-0.6B"}
    assert asyncio.run(workers.unload_model(URL, "qwen-asr-0.6B")) is True


@pytest.mark.parametrize("role", ["all", "stt"])
def test_stt_url_ignored_outside_main(monkeypatch, role):
    from backend import roles

    monkeypatch.setenv("VOICEBOX_ROLE", role)
    monkeypatch.setenv("VOICEBOX_STT_URL", URL)
    assert roles.stt_url() is None
