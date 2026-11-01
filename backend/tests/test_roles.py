"""
Tests for --role (backend/roles.py): route allow-list per role, worker DB
init without migrations, and the stale error cleared on completion.
"""

import asyncio
import sqlite3

import pytest
from fastapi import FastAPI, WebSocket
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import config, roles
from backend.database import Base, Generation, VoiceProfile, session as db_session
from backend.services.history import update_generation_status


def _client(monkeypatch, role):
    monkeypatch.setenv("VOICEBOX_ROLE", role)
    app = FastAPI()
    app.add_middleware(roles.RoleMiddleware)
    for path in ("/health", "/profiles", "/transcribe", "/tts/stream", "/models/status", "/models/qwen-tts-1.7B/unload"):
        app.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
    for path in ("/tts/ws", "/stt/ws"):

        async def ws_ok(ws: WebSocket):
            await ws.accept()
            await ws.send_text("ok")
            await ws.close()

        app.add_api_websocket_route(path, ws_ok)
    return TestClient(app)


@pytest.mark.parametrize(
    "role, allowed, refused",
    [
        ("all", ["/health", "/profiles", "/transcribe", "/tts/stream"], []),
        ("main", ["/health", "/profiles", "/transcribe", "/tts/stream"], []),
        ("tts", ["/health", "/tts/stream", "/models/status", "/models/qwen-tts-1.7B/unload"], ["/profiles", "/transcribe"]),
        ("stt", ["/health", "/transcribe", "/models/status"], ["/profiles", "/tts/stream"]),
    ],
)
def test_http_allow_list(monkeypatch, role, allowed, refused):
    client = _client(monkeypatch, role)
    for path in allowed:
        assert client.get(path).status_code == 200, path
    for path in refused:
        assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("role, allowed, refused", [("tts", "/tts/ws", "/stt/ws"), ("stt", "/stt/ws", "/tts/ws")])
def test_websocket_allow_list(monkeypatch, role, allowed, refused):
    client = _client(monkeypatch, role)
    with client.websocket_connect(allowed) as ws:
        assert ws.receive_text() == "ok"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(refused):
            pass


def test_worker_init_db_skips_migrations(tmp_path, monkeypatch):
    # init_db swaps module globals; register them so monkeypatch restores them.
    for name in ("engine", "SessionLocal", "_db_path"):
        monkeypatch.setattr(db_session, name, getattr(db_session, name))
    monkeypatch.setattr(config, "_data_dir", tmp_path)
    monkeypatch.setenv("VOICEBOX_ROLE", "tts")

    db_session.init_db()

    with sqlite3.connect(config.get_db_path()) as conn:
        assert conn.execute("select name from sqlite_master where type='table'").fetchall() == []


def test_completed_clears_stale_error(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    db.add(VoiceProfile(id="p", name="P"))
    db.add(Generation(id="g", profile_id="p", text="hi", status="failed", error="Server was shut down during generation"))
    db.commit()

    result = asyncio.run(update_generation_status("g", "completed", db, audio_path="a.wav", duration=1.0))

    assert result.status == "completed"
    assert result.error is None


@pytest.mark.parametrize("role", ["all", "main", "stt"])
def test_health_reports_role(monkeypatch, role):
    from backend.routes.health import health

    monkeypatch.setenv("VOICEBOX_ROLE", role)
    assert asyncio.run(health()).role == role


@pytest.mark.parametrize("role, env, expected", [("tts", None, 2048), ("stt", None, 512), ("tts", "300", 300), ("all", "300", None), ("main", None, None)])
def test_worker_mlx_cache_limit(monkeypatch, role, env, expected):
    from backend.backends.base import worker_mlx_cache_limit_mb

    if env is None:
        monkeypatch.delenv("VOICEBOX_MLX_CACHE_LIMIT_MB", raising=False)
    else:
        monkeypatch.setenv("VOICEBOX_MLX_CACHE_LIMIT_MB", env)
    assert worker_mlx_cache_limit_mb(role) == expected


def test_main_refuses_routes_of_its_workers(monkeypatch):
    client = _client(monkeypatch, "main")
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://127.0.0.1:17494")
    response = client.get("/tts/stream")
    assert response.status_code == 404
    assert "http://127.0.0.1:17494/tts/stream" in response.json()["detail"]
    with client.websocket_connect("/tts/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008
    assert "17494" in closed.value.reason
    # No STT worker: main still serves STT itself; /transcribe is forwarded, never refused.
    with client.websocket_connect("/stt/ws") as ws:
        assert ws.receive_text() == "ok"
    assert client.get("/transcribe").status_code == 200
    monkeypatch.setenv("VOICEBOX_STT_URL", "http://127.0.0.1:17495")
    with client.websocket_connect("/stt/ws") as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert client.get("/transcribe").status_code == 200


def test_worker_urls_only_restrict_main(monkeypatch):
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://127.0.0.1:17494")
    assert _client(monkeypatch, "all").get("/tts/stream").status_code == 200
    assert _client(monkeypatch, "tts").get("/tts/stream").status_code == 200


def test_health_reports_worker_urls(monkeypatch):
    from backend.routes.health import health

    monkeypatch.setenv("VOICEBOX_ROLE", "main")
    monkeypatch.setenv("VOICEBOX_TTS_URL", "http://127.0.0.1:17494/")
    monkeypatch.delenv("VOICEBOX_STT_URL", raising=False)
    response = asyncio.run(health())
    assert (response.tts_url, response.stt_url) == ("http://127.0.0.1:17494", None)
