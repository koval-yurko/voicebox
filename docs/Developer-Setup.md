_Set up your local development environment for Voicebox YK (macOS Apple Silicon)_

## Quick Setup (Recommended)

Get started in two commands:

```bash
# Clone and enter the repository
git clone git@github.com:koval-yurko/voicebox.git
cd voicebox

# Setup everything (Python venv, JS deps, dev sidecar)
just setup

# Start development (backend + desktop app)
just dev
```

The `just dev` command automatically starts the Python backend (if not already running) and launches the Tauri desktop app.

## Prerequisites

Ensure you have these installed:

- **Bun**

  [Download Bun](https://bun.sh)
  ```bash
  curl -fsSL https://bun.sh/install | bash
  ```

- **uv** (installs and manages Python 3.12)

  ```bash
  brew install uv
  ```
  `just setup` uses uv to create the venv on Python 3.12. This matches CI. Some dependencies have no 3.13 release yet.

- **Rust**

  [Install Rust](https://rustup.rs)
  ```bash
  rustc --version
  ```

- **Just**

  [Install Just](https://github.com/casey/just)
  ```bash
  brew install just
  ```

## Just Commands

Run `just --list` to see all available commands. Highlights:

### Setup

| Command | Description |
|---------|-------------|
| `just setup` | Full setup (Python venv + JS deps + dev sidecar). Pins the venv to Python 3.12 (recreating a wrong-version one) and installs the MLX dependencies on Apple Silicon. |
| `just setup-python` | Python venv + dependencies only |
| `just setup-js` | `bun install` only |

### Development

| Command | Description |
|---------|-------------|
| `just dev` | Start backend + Tauri desktop app (reuses a running backend if one exists) |
| `just dev-web` | Start backend + web app (no Tauri/Rust build) |
| `just dev-backend` | Backend only |
| `just dev-frontend` | Tauri app only (backend must already be running) |
| `just kill` | Stop all dev processes |

### Build

| Command | Description |
|---------|-------------|
| `just build` | Server binaries + Tauri app |
| `just build-server` | Server binaries only (`voicebox-server` and `voicebox-mcp`) |
| `just build-tauri` | Tauri app only. Signs the update package with `~/.tauri/voicebox-yk.key` if it exists |
| `just build-web` | Web app only |

### Quality

| Command | Description |
|---------|-------------|
| `just check` | Lint + format + typecheck (Biome + ruff) |
| `just fix` | Auto-fix lint + format issues |
| `just lint` / `just format` | Lint or format only |
| `just test` | Run Python tests (pytest) |
| `just test-models` | End-to-end generation against every TTS engine using the frozen binary |

### Database

| Command | Description |
|---------|-------------|
| `just db-init` | Initialize SQLite database |
| `just db-reset` | Delete and reinitialize the database |

### Utilities

| Command | Description |
|---------|-------------|
| `just generate-api` | Generate TypeScript API client from the backend's OpenAPI schema |
| `just docs` | Open `http://localhost:17593/docs` in your browser |
| `just logs` | Tail backend logs |
| `just clean` | Remove build artifacts |
| `just clean-python` | Remove the Python venv + `__pycache__` |
| `just clean-all` | Nuclear clean (includes all `node_modules`) |

## Project Structure

```
voicebox/
  app/
    src/
      components/
      lib/
      hooks/
  backend/
    app.py
    main.py
    config.py
    models.py
    server.py
    routes/
      ...
    services/
      ...
    backends/
      ...
    database/
      ...
    utils/
      ...
  tauri/
    src-tauri/
  web/
  scripts/
```

### Request Flow

HTTP request → **routes/** (validate input) → **services/** (business logic) → **backends/** (TTS/STT inference) → **utils/** (audio processing)

### Key Modules

- **app.py** — FastAPI app factory, CORS, lifecycle events
- **main.py** — Entry point (imports app, runs uvicorn)
- **server.py** — Tauri sidecar launcher, parent-pid watchdog
- **services/generation.py** — Single function handling all generation modes
- **backends/** — TTS/STT engine implementations (MLX, PyTorch, etc.)

## Model Downloads

Models are automatically downloaded from HuggingFace Hub on first use, with live progress streamed to the UI:

- **Whisper** (transcription) — auto-downloads on first transcription
- **TTS engines** — auto-download on first generation. Sizes range from 82 M (Kokoro, ~350 MB) to 3 B (TADA, ~8 GB)

See [Model Management](Dev-Model-Management) for the full list.

> [!WARNING]
> First-time usage will be slower due to model downloads, but subsequent runs will use cached models.

## Generate OpenAPI Client

After starting the backend server, generate the TypeScript API client:

```bash
just generate-api
```

This downloads the OpenAPI schema and generates the TypeScript client in `app/src/lib/api/`.

## Manual Setup (Advanced)

If you prefer not to use Just, follow these manual steps:

### 1. Install JavaScript Dependencies

```bash
bun install
```

This installs dependencies for:
- `app/` - Shared React frontend
- `tauri/` - Tauri desktop wrapper
- `web/` - Web deployment wrapper

### 2. Set Up Python Backend

```bash
cd backend

# Create virtual environment (use Python 3.12 — see Prerequisites)
uv venv --seed --python 3.12 venv

# Activate virtual environment
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Apple Silicon: install MLX dependencies
pip install -r requirements-mlx.txt
pip install --no-deps mlx-lm==0.31.1
pip install --no-deps mlx-audio==0.4.1

# Chatterbox pins numpy<1.26 / torch==2.6 which break on Python 3.12+
pip install --no-deps chatterbox-tts

# HumeAI TADA pins torch>=2.7,<2.8 which conflicts with our torch>=2.1
pip install --no-deps hume-tada

# OmniVoice needs transformers>=5.3; the one class it uses is vendored
pip install --no-deps omnivoice

# Install Qwen3-TTS from source
pip install git+https://github.com/QwenLM/Qwen3-TTS.git

# PyInstaller and linting tools
pip install pyinstaller ruff pytest pytest-asyncio
```

### 3. Start Development

Start the backend from the repo root:
```bash
backend/venv/bin/uvicorn backend.main:app --reload --port 17593
```

In a new terminal, start the desktop app:
```bash
cd tauri
bun run tauri dev
```

## Next Steps

- **[Architecture](Architecture)** — Understand the system architecture
- **[Contributing](https://github.com/koval-yurko/voicebox/blob/main/CONTRIBUTING.md)** — Read the contribution guidelines
- **[Building](Building)** — Learn how to build production releases
- **[TTS Engines](https://github.com/koval-yurko/voicebox/blob/main/docs-internal/tts-engines.mdx)** — Add a new TTS engine end-to-end

## Troubleshooting

<details>
<summary>Backend won't start</summary>

- Check Python version (must be 3.12)
- Ensure virtual environment is activated: `source backend/venv/bin/activate`
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check if port 17593 is available

</details>

<details>
<summary>Tauri build fails</summary>

- Ensure Rust is installed: `rustc --version`
- Clean the build: `cd tauri/src-tauri && cargo clean`
- Try rebuilding: `just dev`

</details>

<details>
<summary>OpenAPI client generation fails</summary>

- Ensure backend is running: `curl http://localhost:17593/openapi.json`
- Check network connectivity
- Verify the backend is accessible at localhost:17593

</details>

See the full [Troubleshooting Guide](Troubleshooting) for more issues and solutions.
