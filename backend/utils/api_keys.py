"""API key gate for external access (for example through a Cloudflare Tunnel).

Requests are *external* when they carry Cloudflare edge headers
(``Cf-Connecting-Ip`` / ``Cf-Ray``) or come from a non-loopback client.
A tunnel connector runs on this machine, so tunnelled requests reach the
server from 127.0.0.1 and only the Cloudflare headers tell them apart.

External requests:
- to the health check (``/health``) and the API docs (``/docs``,
  ``/redoc``, ``/openapi.json``) need no key;
- to every other route must send a valid key as ``Authorization: Bearer <key>``
  or ``X-API-Key``; with no keys configured they are refused (fail closed).

Local requests (the desktop app, local scripts) are not affected.

Keys live in ``<data dir>/api_keys.json``::

    {"keys": [{"name": "livekit-worker", "key": "<random secret>"}]}

The file is re-read when it changes, so keys can be added or revoked
without a restart. ``scripts/voicebox_api_key.py`` manages it.
"""

import hmac
import json
import logging
from ipaddress import ip_address
from pathlib import Path
from typing import Optional

from starlette.responses import JSONResponse
from starlette.websockets import WebSocketClose

from .. import config

logger = logging.getLogger(__name__)

KEYS_FILENAME = "api_keys.json"
# External requests to these paths need no key: health checks and the API docs.
# Exact paths only: /health/filesystem and other /health/* routes reveal local paths.
PUBLIC_PATHS = ("/health", "/docs", "/redoc", "/openapi.json")
CLOUDFLARE_HEADERS = (b"cf-connecting-ip", b"cf-ray")


class APIKeyStore:
    """Keys from ``<data dir>/api_keys.json``, cached until the file changes."""

    def __init__(self):
        self._stamp = None
        self._keys: list[tuple[str, bytes]] = []

    def _path(self) -> Path:
        return config.get_data_dir() / KEYS_FILENAME

    def _load(self) -> list[tuple[str, bytes]]:
        path = self._path()
        try:
            st = path.stat()
        except FileNotFoundError:
            self._stamp, self._keys = None, []
            return self._keys
        stamp = (str(path), st.st_mtime_ns, st.st_size)
        if stamp != self._stamp:
            try:
                data = json.loads(path.read_text())
                self._keys = [
                    (str(k.get("name") or "unnamed"), str(k["key"]).encode())
                    for k in data.get("keys", [])
                    if k.get("key")
                ]
            except Exception as e:
                logger.error("Cannot read %s: %s; refusing all external requests", path, e)
                self._keys = []
            self._stamp = stamp
        return self._keys

    def match(self, key: Optional[str]) -> Optional[str]:
        """Return the key's name when ``key`` is valid, else None."""
        if not key:
            return None
        candidate = key.encode()
        name = None
        for key_name, secret in self._load():
            # Compare against every key so timing does not reveal which one matched.
            if hmac.compare_digest(candidate, secret):
                name = key_name
        return name


def _header(scope, name: bytes) -> Optional[str]:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return None


def is_external(scope) -> bool:
    if any(_header(scope, h) is not None for h in CLOUDFLARE_HEADERS):
        return True
    client = scope.get("client")
    if not client:
        return False
    try:
        return not ip_address(client[0]).is_loopback
    except ValueError:
        return True  # not an IP (e.g. a test client name): treat as external


def request_key(scope) -> Optional[str]:
    auth = _header(scope, b"authorization")
    if auth and auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return _header(scope, b"x-api-key")


class APIKeyMiddleware:
    """ASGI middleware that applies the external-access rules to HTTP and WebSocket."""

    def __init__(self, app, store: Optional[APIKeyStore] = None):
        self.app = app
        self.store = store or APIKeyStore()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not is_external(scope):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if scope["type"] == "http" and path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        name = self.store.match(request_key(scope))
        if name is None:
            logger.warning("Rejected external %s %s: missing or invalid API key", scope["type"], path)
            await self._deny(scope, receive, send, 401, "Invalid or missing API key")
            return

        scope.setdefault("state", {})["api_key_name"] = name
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, status: int, detail: str):
        if scope["type"] == "websocket":
            # Closing before accept makes the server answer the upgrade with 403.
            await WebSocketClose(code=1008, reason=detail)(scope, receive, send)
        else:
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, send)


def add_openapi_security(app) -> None:
    """Declare the API key in the OpenAPI schema so Swagger UI shows an Authorize button.

    Routes that run on a TTS/STT worker are left out: callers must use the worker's own URL.
    """
    from ..roles import worker_for_path

    base_openapi = app.openapi

    def openapi():
        if not app.openapi_schema:
            schema = base_openapi()  # also caches the schema on app.openapi_schema
            schema.setdefault("components", {})["securitySchemes"] = {
                "APIKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
            }
            schema["security"] = [{"APIKey": []}]
            for path in [p for p in schema.get("paths", {}) if worker_for_path(p)]:
                del schema["paths"][path]
        return app.openapi_schema

    app.openapi = openapi
