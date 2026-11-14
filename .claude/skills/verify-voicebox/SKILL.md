---
name: verify-voicebox
description: Drive a disposable Voicebox YK backend (FastAPI HTTP API, the surface the desktop app, LiveKit agents and MCP clients use) to prove TTS, STT, voice profile and API key behavior with captured evidence. Use when you must show a backend change works for real, not only in unit tests, or when asked to verify, smoke-test or demo Voicebox behavior.
---

# Verify Voicebox YK

Voicebox YK is a local voice I/O app: a Tauri desktop shell plus a Python FastAPI backend (`backend/server.py`). Every user path ends in the backend HTTP API: the desktop UI calls it, LiveKit agents call it through the Cloudflare Tunnel, and MCP clients call `/mcp`. This skill drives that API on a disposable instance.

It also covers the streaming routes the LiveKit plugin (`integrations/livekit/`) uses (`/stt/ws`, `/tts/ws`, `/tts/stream`) and the MCP server at `/mcp/`. Not covered: the Tauri window (`just dev`) and the browser UI (`just dev-web`, Vite on `web/`).

The helper is `.claude/skills/verify-voicebox/scripts/vb-verify`. Below it is `$V`:

```bash
V=.claude/skills/verify-voicebox/scripts/vb-verify   # run from the repo root (or a worktree root)
```

## Never touch the installed apps

The user runs the installed Voicebox YK (main 17593, TTS worker 17494, STT worker 17495) and the original Voicebox (17493). They hold real profiles, history and API keys.

- Do not call those ports. Do not use `just dev`, `just dev-backend`, `just dev-web` or `bun run dev:server` for verification: they bind 17593 or reuse whatever answers there.
- Do not run `just kill`. It kills every `uvicorn backend.main:app` and every `vite` on the machine.
- `scripts/voicebox_api_key.py` defaults to the installed app's data dir. Always pass `--data-dir`.
- Do not call `DELETE /models/{name}` or `POST /models/migrate`. Models live in the shared Hugging Face cache (`~/.cache/huggingface/hub`, about 21 GB), which the installed apps also use.

`vb-verify` uses base port 17693 (+1 TTS, +2 STT in split mode), refuses protected or busy ports, and kills only the PIDs it started.

## Launch

```bash
$V up            # one process, --role all, port 17693
$V up --split    # main 17693 + TTS worker 17694 + STT worker 17695, like the installed app
```

- Python: `backend/venv`, else the main checkout's `backend/venv` (a worktree has none), else `$VB_PYTHON`. The code always comes from the current checkout.
- Data: a fresh dir `.verify/runs/<run-id>/data`. The server creates the SQLite DB and runs migrations on start, so an empty dir is enough.
- Ready: prints `ready: http://127.0.0.1:17693  run=<run-id>`. That means `/health` reports the expected role and `GET /profiles` answers (the DB is usable). A cold start takes about 7 s. The limit is 180 s per process.
- Models are not loaded at start. The first TTS or STT call loads the model (seconds for cached models). An uncached model starts a download and the call returns HTTP 202. See the feature files.
- `VB_VERIFY_PORT=<base>` moves all ports. `VB_RUN_ID=<id>` names the run.
- Only one run at a time per checkout. `up` refuses while `.verify/current` exists.

## Doctor

Run it first, and again whenever anything looks wrong. It is read-only.

```bash
$V doctor
```

Expected output, all `ok`, exit code 0:

```
ok   all pid=<pid> port=17693
ok   version 0.8.1
ok   data dir is the disposable one
```

It checks that each recorded PID is alive and owns its port, that `/health` reports the right role, that `GET /` returns the version in `backend/__init__.py`, and that `/health/filesystem` reports the disposable data dir. Any `FAIL` means: do not drive this instance. Run `$V down`, then `$V up`.

## Drive

```bash
$V curl <evidence-name> <path> [curl args...]   # HTTP call on the main port, saved as evidence
$V wav <evidence-name> "<text>"                 # spoken 16 kHz mono WAV from macOS `say`, saved as evidence
$V client stt-ws <evidence-name> <wav> [model]  # stream a WAV to /stt/ws on the STT worker
$V client tts-ws <evidence-name> <profile-id> "<text>"  # stream text to /tts/ws on the TTS worker, saves <name>.wav
$V client mcp <evidence-name> <tool> '<json args>'      # call an MCP tool on main
```

- `curl` prints the body to stdout and `HTTP <code> -> <file>` to stderr. It writes `<name>.cmd`, `<name>.out` and `<name>.code`.
- Loopback requests need no API key. To act as an external (tunnel) client, add `-H 'Cf-Ray: verify'`. The server then treats the request as external. See [external access](features/external-access.md).
- Long jobs: `POST /generate` returns `status: "generating"` at once. Poll `$V curl <name> /history/<id>` until `status` is `completed` or `failed`, or read the SSE stream `curl -sN --max-time 120 http://127.0.0.1:17693/generate/<id>/status`.
- In split mode main forwards `/generate` to the TTS worker (17694) and `/transcribe` to the STT worker (17695). Drive main for those, as the desktop app does.
- Streaming routes run only on the workers, as for the LiveKit plugin. Main answers them with the worker URL. `$V client` picks the right port. For `curl`, prefix the path: `$V curl <name> tts:/tts/stream ...`. In single mode the prefix falls back to the one port.
- `$V client` uses the venv Python (`websockets`, `fastmcp`), so it works only with the venv that `up` uses.

The feature recipes are in [features/README.md](features/README.md). Read the index, then the feature file.

## Evidence

Evidence goes to `.verify/evidence/<run-id>/` (git-ignored) and survives `down`. Server logs are in `logs/<role>.log` there.

Proof standards:

- Use the real user path: the public HTTP routes the app, LiveKit plugin or MCP client call. Do not call Python functions or edit the DB.
- Capture the action and the resulting state: the request (`.cmd`), the response (`.out`, `.code`) and a second read-only view (for example `GET /history/<id>` after `POST /generate`, `GET /profiles` after `POST /profiles`).
- Check side effects: audio files exist and play (`GET /audio/<id>` returns a WAV with a size above 0), rows appear in list endpoints.
- Prove generated speech by its content: transcribe the generated WAV with `/transcribe` and check the text starts with what you asked for.
- No mocks. All engines run locally on this Mac (MLX).
- Report the run ID, the feature ID and the evidence file names with every claim.

## Cleanup

```bash
$V down
```

It sends TERM to the PIDs in `.verify/runs/<run-id>/pids`, waits up to 10 s, sends KILL to what is left, then deletes `.verify/runs/<run-id>` (the data dir) and `.verify/current`. It keeps `.verify/evidence/<run-id>/`. Run it after every failed attempt too. Check that nothing is left: `pgrep -fl 'verify/runs'` prints nothing.

## Helpers

| Command | What it does |
| --- | --- |
| `$V up [--split]` | Start a disposable backend and wait until it is usable |
| `$V doctor` | Read-only health, ownership, version and data dir check |
| `$V curl <name> [tts:\|stt:]<path> [args]` | HTTP call on main (or a worker), saved as evidence |
| `$V wav <name> "<text>"` | Spoken test audio (macOS `say`), saved as evidence |
| `$V client <stt-ws\|tts-ws\|mcp> <name> ...` | WebSocket and MCP clients (`scripts/vb-client.py`), events saved as evidence |
| `$V down` | Stop own processes, delete run data, keep evidence |

Keep the feature map current with `/pstack:maintain-verification-skill`.
