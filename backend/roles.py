"""Process roles: ``all`` (default, 1 process), ``main``, and the ``tts`` / ``stt`` workers.

Main owns all state (DB writes, migrations, queue, cleanup). A worker only
serves its inference routes and refuses the rest, so a wrong URL fails at
once instead of writing state from the wrong process. Main with a worker URL
refuses that worker's own routes, so a call to main never loads a model there.
"""

import os
import re

from starlette.responses import JSONResponse
from starlette.websockets import WebSocketClose

ROLES = ("all", "main", "tts", "stt")

_SHARED_PATHS = {"/health", "/models/load", "/models/unload", "/models/status", "/shutdown", "/watchdog/disable"}
_WORKER_PATHS = {
    "tts": {"/tts/stream", "/tts/warmup", "/tts/ws", "/tts/render"},
    "stt": {"/stt/ws", "/transcribe"},
}
_MODEL_UNLOAD = re.compile(r"/models/[^/]+/unload")
# Worker routes that main does not forward. /transcribe is not here: main forwards it.
_WORKER_ONLY_PATHS = {
    "tts": {"/tts/stream", "/tts/warmup", "/tts/ws", "/tts/render"},
    "stt": {"/stt/ws"},
}


def set_role(role: str) -> None:
    # Env var, so code that imports the app before argparse runs still sees it.
    os.environ["VOICEBOX_ROLE"] = role


def get_role() -> str:
    return os.environ.get("VOICEBOX_ROLE", "all")


def stt_url() -> str | None:
    """STT worker URL when this process is main and one was given, else None (transcribe locally)."""
    url = os.environ.get("VOICEBOX_STT_URL")
    return url.rstrip("/") if url and get_role() == "main" else None


def tts_url() -> str | None:
    """TTS worker URL when this process is main and one was given, else None (render locally)."""
    url = os.environ.get("VOICEBOX_TTS_URL")
    return url.rstrip("/") if url and get_role() == "main" else None


def is_worker() -> bool:
    return get_role() in _WORKER_PATHS


def worker_for_path(path: str) -> str | None:
    """URL of the worker that serves ``path`` when this main must not run it itself."""
    for worker, url in (("tts", tts_url()), ("stt", stt_url())):
        if url and path in _WORKER_ONLY_PATHS[worker]:
            return url
    return None


def path_allowed(path: str) -> bool:
    worker_paths = _WORKER_PATHS.get(get_role())
    if worker_paths is None:
        return worker_for_path(path) is None
    return path in _SHARED_PATHS or path in worker_paths or bool(_MODEL_UNLOAD.fullmatch(path))


class RoleMiddleware:
    """Pure ASGI, so it also filters WebSocket routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] in ("http", "websocket") and not path_allowed(path):
            worker = worker_for_path(path)
            if worker:
                detail = f"{path} runs on the worker at {worker}, not on main. Call {worker}{path}."
            else:
                detail = f"Not served by the {get_role()} worker"
            if scope["type"] == "websocket" and worker:
                # Accept first: a close before the handshake reaches the client as HTTP 403 without the reason.
                await receive()
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": 1008, "reason": detail[:120]})
                return
            if scope["type"] == "websocket":
                response = WebSocketClose(code=1008)
            else:
                response = JSONResponse({"detail": detail}, status_code=404)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
