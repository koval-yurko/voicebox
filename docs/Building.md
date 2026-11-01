_How Voicebox YK is built for production_

## Overview

Voicebox YK uses a two-stage build process:

1. **Python Server Binary** — PyInstaller bundles the FastAPI backend into a standalone executable
2. **Tauri Desktop App** — Bundles the React frontend, Rust wrapper, and Python server as a sidecar

## Build Commands

```bash
just build          # Build everything (server + Tauri)
just build-server   # Build Python server binary only
just build-tauri    # Build Tauri app only
```

## Server Binary Build

### Build Script

`scripts/build-server.sh` orchestrates the build:

```bash
# Determine platform (aarch64-apple-darwin)
PLATFORM=$(rustc --print host-tuple)

# Run PyInstaller via build_binary.py
cd backend
python build_binary.py         # voicebox-server
python build_binary.py --shim  # voicebox-mcp

# Copy both sidecars to Tauri's binaries directory
cp dist/voicebox-server ../tauri/src-tauri/binaries/voicebox-server-${PLATFORM}
cp dist/voicebox-mcp ../tauri/src-tauri/binaries/voicebox-mcp-${PLATFORM}
```

### PyInstaller Configuration

`backend/build_binary.py` contains the PyInstaller configuration:

**Entry Point:** Uses `server.py` (not `main.py`) for Tauri sidecar support

**Key Options:**
- `--onefile` — Single executable
- `--hidden-import` — Explicitly import modules PyInstaller can't detect
- `--collect-all` — Bundle data files and native libraries for packages like `mlx`, `zipvoice`
- `--exclude-module` — Strip NVIDIA packages from CPU builds

**Apple Silicon Logic:**

```python
# Apple Silicon — include MLX backend
if is_apple_silicon():
    args.extend([
        "--hidden-import", "mlx",
        "--collect-all", "mlx",        # Bundles .dylib and .metallib files
    ])
```

**Environment Variable:**

```bash
export QWEN_TTS_PATH=~/path/to/Qwen3-TTS  # Use local Qwen3-TTS source
```

## Tauri App Build

Tauri bundles everything together:

```bash
cd tauri
bun run tauri build
```

**What happens:**
1. Vite builds the React frontend
2. Rust compiles the Tauri wrapper
3. Sidecar binary is copied from `src-tauri/binaries/`
4. The macOS `.app` bundle and DMG are created, plus the signed `.app.tar.gz` update package

**Output locations:**

```
tauri/src-tauri/target/release/bundle/
  macos/
  dmg/
```

### Sidecar Configuration

The server binary is declared as an external binary in `tauri.conf.json`:

```json
{
  "bundle": {
    "externalBin": ["binaries/voicebox-server", "binaries/voicebox-mcp"]
  }
}
```

Tauri looks for `voicebox-server-${PLATFORM}` and `voicebox-mcp-${PLATFORM}` in `src-tauri/binaries/` and bundles them.

## GitHub Actions Release

`.github/workflows/release.yml` automates the full build:

It has one job. It runs on `macos-latest` and builds for `aarch64-apple-darwin` with the MLX backend. It runs on a `v*` tag push or by hand.

### Build Steps

1. **Setup** — LLVM, Python 3.12, Python and MLX dependencies
2. **Build Server** — `scripts/build-server.sh`
3. **Setup Tauri** — Bun, Rust (`aarch64-apple-darwin` target), `bun install`
4. **Release Notes** — Take the section for this version from `CHANGELOG.md`
5. **Build Tauri** — `tauri-action` builds the app and publishes the release with `latest.json`

### Code Signing

- The update package is signed with the Tauri signing key (`TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` secrets). See [Auto-Updater](Autoupdater).
- The app is not signed with an Apple Developer certificate.

## Troubleshooting

<details>
<summary>Binary not found in dist/</summary>

PyInstaller failed to create the output. Check:
- Python venv is activated
- All dependencies installed: `pip install -r requirements.txt`
- PyInstaller installed: `pip install pyinstaller`

</details>

<details>
<summary>MLX/Metal libraries missing in bundle</summary>

macOS Apple Silicon builds need `--collect-all mlx` to include `.dylib` and `.metallib` files, not just `--collect-data`.

</details>

<details>
<summary>Tauri can't find sidecar</summary>

Ensure `voicebox-server-${PLATFORM}` and `voicebox-mcp-${PLATFORM}` exist in `tauri/src-tauri/binaries/` before running Tauri build.

</details>
