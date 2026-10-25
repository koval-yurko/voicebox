#!/usr/bin/env python3
"""Check the external-access API key rules (backend/utils/api_keys.py).

Runs the real app in-process with a temporary data dir, no models loaded.
Usage: backend/venv/bin/python scripts/test_api_keys.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from backend import config  # noqa: E402
from backend.app import app  # noqa: E402

KEY = "vbx_test_key_123"
CF = {"Cf-Connecting-Ip": "203.0.113.7", "Cf-Ray": "abc-LHR"}
failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


def ws_status(client, headers):
    """'accepted' if the upgrade succeeds, else the close/denial info."""
    try:
        with client.websocket_connect("/tts/ws", headers=headers) as ws:
            ws.send_json({"type": "bogus"})
            msg = ws.receive_json()
            return "accepted", msg
    except WebSocketDisconnect as e:
        return "rejected", e.code
    except Exception as e:  # WebSocketDenialResponse on newer Starlette
        return "rejected", getattr(e, "status_code", type(e).__name__)


tmp = Path(tempfile.mkdtemp())
config.set_data_dir(tmp)
local = TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)
remote_ip = TestClient(app, client=("198.51.100.20", 50000), raise_server_exceptions=False)

# 1. No keys file: external is refused, local works.
r = local.get("/tts/stream", headers=CF)
check("no keys file: external /tts refused", r.status_code == 401, r.status_code)
r = local.get("/health")
check("local /health unaffected", r.status_code == 200, r.status_code)

(tmp / "api_keys.json").write_text(json.dumps({"keys": [{"name": "livekit-worker", "key": KEY}]}))

# 2. HTTP through Cloudflare headers (client IP is loopback, as with cloudflared).
r = local.post("/tts/stream", headers=CF, json={})
check("CF, no key -> 401", r.status_code == 401, r.status_code)
r = local.post("/tts/stream", headers={**CF, "Authorization": "Bearer wrong"}, json={})
check("CF, wrong key -> 401", r.status_code == 401, r.status_code)
r = local.post("/tts/stream", headers={**CF, "Authorization": f"Bearer {KEY}"}, json={})
check("CF, valid Bearer key -> passes auth", r.status_code not in (401, 403), r.status_code)
r = local.post("/tts/stream", headers={**CF, "X-API-Key": KEY}, json={})
check("CF, valid X-API-Key -> passes auth", r.status_code not in (401, 403), r.status_code)
r = local.get("/health", headers={**CF, "Authorization": f"Bearer {KEY}"})
check("CF, non-/tts route with key -> 403", r.status_code == 403, r.status_code)
r = local.post("/shutdown", headers={**CF, "Authorization": f"Bearer {KEY}"})
check("CF, /shutdown with key -> 403", r.status_code == 403, r.status_code)
r = local.get("/mcp/", headers={**CF, "Authorization": f"Bearer {KEY}"})
check("CF, /mcp with key -> 403", r.status_code == 403, r.status_code)

# 3. Non-loopback client IP without Cloudflare headers (remote mode / LAN).
r = remote_ip.get("/health")
check("LAN client, /health -> 403", r.status_code == 403, r.status_code)
r = remote_ip.post("/tts/stream", json={})
check("LAN client, /tts no key -> 401", r.status_code == 401, r.status_code)

# 4. Local requests without Cloudflare headers keep working without a key.
r = local.post("/tts/stream", json={})
check("local /tts without key -> passes auth", r.status_code not in (401, 403), r.status_code)

# 5. WebSocket.
st = ws_status(local, CF)
check("WS via CF, no key -> rejected", st[0] == "rejected", st)
st = ws_status(local, {**CF, "Authorization": "Bearer wrong"})
check("WS via CF, wrong key -> rejected", st[0] == "rejected", st)
st = ws_status(local, {**CF, "Authorization": f"Bearer {KEY}"})
check("WS via CF, valid key -> accepted", st[0] == "accepted", st)
st = ws_status(local, {})
check("WS local, no key -> accepted", st[0] == "accepted", st)

# 6. Revoke by rewriting the file: picked up without restart.
time.sleep(0.01)
(tmp / "api_keys.json").write_text(json.dumps({"keys": []}))
r = local.post("/tts/stream", headers={**CF, "Authorization": f"Bearer {KEY}"}, json={})
check("revoked key -> 401 without restart", r.status_code == 401, r.status_code)

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
