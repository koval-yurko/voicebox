_Understanding Voicebox YK's technical architecture_

## System Overview

Voicebox YK uses a client-server architecture with a React frontend and Python backend. The desktop app is built with Tauri and contains two main layers:

**Frontend Layer:** A React application that handles the UI components, state management with Zustand, and data fetching with React Query (TanStack Query).

**Backend Layer:** A Python FastAPI server that hosts the REST API, runs a pluggable registry of TTS and STT engines, manages the SQLite database, and handles audio processing.

These two layers communicate via HTTP on `localhost:17593`, with the frontend making API requests to the backend. In production the backend is compiled with PyInstaller and launched as a Tauri sidecar; in development it's run manually via `uvicorn`. The desktop app runs the backend as 3 processes by default (see [Server Processes](#server-processes)); the frontend still talks to 1 URL.

## Frontend Architecture

### Tech Stack

- **Framework**: React 18 with TypeScript
- **State Management**: Zustand stores
- **Data Fetching**: React Query (TanStack Query)
- **Styling**: Tailwind CSS
- **Audio**: WaveSurfer.js
- **Desktop**: Tauri (Rust)

### Component Structure

```
app/src/
  components/
    Profiles/
    Generation/
    Stories/
    ServerSettings/
  lib/
    api/
    constants/
    hooks/
    utils/
  stores/
```

## Backend Architecture

### Tech Stack

- **Framework**: FastAPI (Python 3.12)
- **TTS Engines**: Qwen3-TTS, Qwen CustomVoice, LuxTTS, Chatterbox, Chatterbox Turbo, TADA, Kokoro
- **Transcription**: Whisper (PyTorch or MLX-Whisper)
- **Inference Backends**: MLX on Apple Silicon (Metal). PyTorch for engines without an MLX port.
- **Database**: SQLite via SQLAlchemy
- **Audio**: librosa, soundfile, Pedalboard

### Layout

```
backend/
  app.py
  main.py
  config.py
  models.py
  server.py
  build_binary.py
  routes/
    profiles.py
    generate.py
    history.py
    models.py
    channels.py
  services/
    generation.py
    task_queue.py
    profiles.py
    channels.py
  backends/
    __init__.py
    base.py
    pytorch_backend.py
    mlx_backend.py
    qwen_custom_voice_backend.py
    luxtts_backend.py
    chatterbox_backend.py
    chatterbox_turbo_backend.py
    hume_backend.py
    kokoro_backend.py
  database/
    models.py
    session.py
  utils/
    audio.py
    effects.py
```

### Request Flow

An HTTP request enters a **route handler**, which validates input and delegates to a **service** function. The service calls into the appropriate **engine backend** via the registry, which runs the actual inference. Audio post-processing runs through **utils** (trim, resample, effects).

Route handlers are intentionally thin — they validate input, delegate to a service function, and format the response. All business logic lives in `services/`.

### Multi-Engine Registry

The backend is designed so that adding a new TTS engine only requires touching the `backends/` directory and the central registry. There is no per-engine branching in routes or services.

- **`TTSBackend` Protocol** (`backends/__init__.py`) — defines the contract every engine implements: `load_model`, `create_voice_prompt`, `combine_voice_prompts`, `generate`, `unload_model`, `is_loaded`, `_get_model_path`.
- **`ModelConfig` dataclass** — central metadata record for each model variant: `model_name`, `display_name`, `engine`, `hf_repo_id`, `size_mb`, `needs_trim`, `languages`, `supports_instruct`, etc.
- **`TTS_ENGINES` dict** — maps engine name (`"qwen"`, `"kokoro"`, etc.) to display name.
- **`get_tts_backend_for_engine(engine)`** — thread-safe factory that lazily instantiates and caches the backend for an engine using double-checked locking.

Shipped engines:

| Engine key | Display name | Profile type |
|------------|--------------|--------------|
| `qwen` | Qwen TTS | Cloned |
| `qwen_custom_voice` | Qwen CustomVoice | Preset |
| `luxtts` | LuxTTS | Cloned |
| `chatterbox` | Chatterbox TTS | Cloned |
| `chatterbox_turbo` | Chatterbox Turbo | Cloned |
| `tada` | TADA | Cloned |
| `kokoro` | Kokoro | Preset |

See [TTS Engines](https://github.com/koval-yurko/voicebox/blob/main/docs-internal/tts-engines.mdx) for the full contract and integration phases.

### Key Modules

- **`app.py`** — FastAPI app factory, CORS, lifecycle events
- **`main.py`** — Entry point (imports app, runs uvicorn)
- **`server.py`** — Tauri sidecar launcher, parent-pid watchdog, frozen-build environment setup
- **`services/generation.py`** — Single function handling all generation modes (generate, retry, regenerate)
- **`services/task_queue.py`** — Serial generation queue for GPU inference
- **`backends/__init__.py`** — Protocol definitions, `ModelConfig` registry, and engine factory
- **`backends/base.py`** — Shared utilities across all engine implementations (device selection, progress tracking, output trimming)

### Inference Backend Selection

Voicebox YK runs only on macOS Apple Silicon. All inference is local.

| Platform | Backend | Acceleration |
|----------|---------|--------------|
| macOS (Apple Silicon) | MLX | Metal |
| macOS (Apple Silicon) | PyTorch | MPS or CPU, for engines without an MLX port |

### Data Model

Core tables (see `backend/database/models.py`):

- **`profiles`** — Voice profiles with `voice_type` discriminator (`cloned` | `preset` | `designed`), `preset_engine`, `preset_voice_id`, and `default_engine`.
- **`profile_samples`** — Reference audio clips + transcripts for cloned profiles. Empty for preset profiles.
- **`generations`** — Generated audio with text, engine, model, language, seed, and duration.
- **`generation_versions`** — Processed variants of a generation with different effects chains applied.
- **`audio_channels`** + **`channel_device_mappings`** + **`profile_channel_mappings`** — Multi-output routing.

See [Voice Profiles](Dev-Voice-Profiles) and [Effects Pipeline](Dev-Effects-Pipeline) for details.

## Server Processes

The same server binary runs in 1 of 4 roles, set with `--role`:

| Role | Default port | What it does |
|---|---|---|
| `all` (default) | 17593 | Everything in 1 process: state, TTS and STT. Used by `uvicorn` and the desktop app with the setting off. |
| `main` | the Server URL port | Owns all state: database, files, profiles, history, captures, settings, job queue, migrations, startup cleanup. Loads no model. |
| `tts` | 17494 | TTS worker: `WS /tts/ws`, `POST /tts/stream`, `POST /tts/render`, `POST /tts/warmup`. |
| `stt` | 17495 | STT worker: `WS /stt/ws`, `POST /transcribe`. |

Every role also serves `/health` and the model load, unload and status routes. A worker returns 404 for any other path, so a wrong URL fails at once instead of writing state from the wrong process.

**How main uses the workers.** Main gets the worker URLs from `--tts-url` and `--stt-url`:

- **Generation** (`/generate`, `/speak`, MCP speak, `/generate/stream`, `/v1/audio/speech`): main calls `POST /tts/render` on the TTS worker, then normalizes, applies effects, saves the file and writes the database.
- **Transcription** (`/transcribe`, captures, retranscribe, MCP transcribe): main calls `POST /transcribe` on the STT worker and saves the text.
- **Model status, load and unload**: main asks the worker that owns the model type.
- **Streaming routes** (`/tts/ws`, `/tts/stream`, `/tts/warmup`, `/tts/render`, `/stt/ws`): main does not run them. It returns 404, or closes a WebSocket with code 1008, with a message that names the worker URL. Streaming clients, such as the LiveKit plugins in `integrations/livekit`, connect to the workers directly.

Main's `/health` reports `role`, `tts_url` and `stt_url`. Settings → General shows a card for each worker with its URL and routes.

**Why.** On Apple Silicon, MLX runs one model at a time per process. In 1 process, a transcript waits until a long generation ends (about 8 s, or no result). With separate processes, the transcript arrives in about 0.2–0.3 s while TTS generates. Each worker sets an MLX cache limit (TTS 2048 MB, STT 512 MB; override with `VOICEBOX_MLX_CACHE_LIMIT_MB`). Measurements: `docs-internal/research/mlx-multi-process.md`.

**Who starts them.** The desktop app starts main, waits until it is ready, then starts both workers with the same data directory, models directory and `--parent-pid`. It stops all 3 on quit or restart, and restarts a worker that exits on its own (at most 3 times in 60 s). Settings → General → *Separate TTS and STT processes* (on by default) turns the workers off; main then runs as `all` from the next app start.

Run the same setup by hand (start the workers after main answers `/health`; add the same `--data-dir` to all 3 if you use one):

```bash
python -m backend.server --role main --port 17593 --tts-url http://127.0.0.1:17494 --stt-url http://127.0.0.1:17495
python -m backend.server --role tts --port 17494
python -m backend.server --role stt --port 17495
```

## Desktop App (Tauri)

### Rust Backend

```
tauri/src-tauri/
  Cargo.toml
  tauri.conf.json
  src/
  binaries/
```

### Responsibilities

- Launch the Python backend as sidecar processes: main plus the TTS and STT workers, or 1 process when the workers are off
- Native file dialogs
- System tray integration
- Auto-updates (Tauri updater)
- Parent-PID watchdog so the backend exits if the app crashes

## Build Process

### Development

```bash
just dev              # Starts backend + Tauri app
just dev-web          # Starts backend + web app (no Tauri)
just dev-backend      # Backend only
just dev-frontend     # Tauri app only (backend must be running)
```

### Production

```bash
just build            # Server binaries + Tauri app
just build-server     # Server binaries only
just build-tauri      # Tauri app only
```

See [Building](Building) for what PyInstaller does and how the release is built.

## Data Flow

### Generation Flow

1. **User Input** — text entered in a React component, engine + profile selected
2. **State Update** — Zustand generation form store records the request
3. **API Request** — React Query mutation hits `POST /generate`
4. **Route** — `routes/generate.py` validates input, dispatches to `services/generation.py`
5. **Voice Prompt** — the service creates or retrieves a cached voice prompt via the engine's backend (in the TTS worker when main runs with workers)
6. **Queue** — `services/task_queue.py` serializes generation to avoid GPU contention
7. **Inference** — the engine backend runs `generate()` and returns audio + sample rate; with workers, main sends steps 5–7 to the TTS worker's `POST /tts/render`
8. **Post-process** — optional trim (for engines that need it), effects chain applied per generation version
9. **Storage** — audio written to the generations directory, metadata saved to SQLite
10. **Response** — backend returns the generation record; frontend updates React Query cache and plays audio

## Performance Considerations

### Frontend

- **Code splitting** — lazy-load routes
- **Memoization** — `React.memo` for heavy components
- **Virtual scrolling** — for large lists
- **Debouncing** — search and input handling

### Backend

- **Async I/O** — all I/O is async; inference runs in `asyncio.to_thread`
- **Serial task queue** — avoids multiple engines fighting for the GPU
- **Voice prompt caching** — engine-specific, keyed by audio hash + reference text
- **Model pinning** — only one model per engine loaded at a time; switching unloads the previous one
- **Per-engine backend cache** — engines are only instantiated once per process (TTS engines live in the TTS worker, STT engines in the STT worker)

## Security

### Current

- Local-only by default (bound to `127.0.0.1:17593`)
- No authentication (localhost trust)
- File system sandboxing via Tauri

### Planned

- API key authentication for remote mode
- User accounts
- Rate limiting
- HTTPS support

## Deployment Modes

### Local Mode

- Backend runs as sidecar
- All data stays on device
- No network required

### Remote Mode

- Backend on a separate machine
- Frontend (desktop or web) connects over HTTP

## Next Steps

- **[Development Setup](Developer-Setup)** — Set up your dev environment
- **[TTS Engines](https://github.com/koval-yurko/voicebox/blob/main/docs-internal/tts-engines.mdx)** — How to add a new engine
- **[Contributing](https://github.com/koval-yurko/voicebox/blob/main/CONTRIBUTING.md)** — Contribute to Voicebox
