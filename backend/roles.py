"""Process roles: ``all`` (default, 1 process), ``main``, and the ``tts`` / ``stt`` workers.

Main owns all state (DB writes, migrations, queue, cleanup). A worker only
serves its inference routes and refuses the rest, so a wrong URL fails at
once instead of writing state from the wrong process.
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


def set_role(role: str) -> None:
    # Env var, so code that imports the app before argparse runs still sees it.
    os.environ["VOICEBOX_ROLE"] = role


def get_role() -> str:
    return os.environ.get("VOICEBOX_ROLE", "all")


def is_worker() -> bool:
    return get_role() in _WORKER_PATHS


def path_allowed(path: str) -> bool:
    worker_paths = _WORKER_PATHS.get(get_role())
    if worker_paths is None:
        return True
    return path in _SHARED_PATHS or path in worker_paths or bool(_MODEL_UNLOAD.fullmatch(path))


class RoleMiddleware:
    """Pure ASGI, so it also filters WebSocket routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not path_allowed(scope["path"]):
            if scope["type"] == "websocket":
                response = WebSocketClose(code=1008)
            else:
                response = JSONResponse({"detail": f"Not served by the {get_role()} worker"}, status_code=404)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
