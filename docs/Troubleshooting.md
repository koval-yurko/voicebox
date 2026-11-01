_Common issues and solutions for Voicebox_

This guide covers common issues you might encounter when using or developing Voicebox, along with solutions.

## Installation Issues

### macOS: "App is damaged and can't be opened"

This occurs because the app isn't signed or notarized by Apple.

**Solution:**
```bash
# Remove the quarantine attribute
xattr -dr com.apple.quarantine "/Applications/Voicebox YK.app"
```

You can also right-click the app in Finder and choose **Open**.

## Server Issues

### Backend Server Won't Start

**Symptoms:**
- Red status indicator in bottom-left corner
- "Failed to connect to server" error

**Solutions:**

<details>
<summary>Port Already in Use</summary>

Check if port 17593 is already in use:

```bash
lsof -i :17593
```

Kill the process using the port:
```bash
kill -9 <PID>
```

</details>

<details>
<summary>Permission Issues</summary>

The server binary might not have execute permissions:

```bash
chmod +x ~/Library/Application\ Support/com.koval-yurko.voicebox-yk/backend/voicebox-server
```

</details>

<details>
<summary>Check Logs</summary>

View server logs for errors:

```bash
tail -f ~/Library/Application\ Support/com.koval-yurko.voicebox-yk/logs/server.log
```

</details>

### `flash-attn is not installed` Warning in Server Logs

**Symptoms:**
```
Warning: flash-attn is not installed. Will only run the manual PyTorch version.
Please install flash-attn for faster inference.
```

**This is harmless.** The warning is emitted by our transformer-based engines (Chatterbox / Qwen) on every startup. FlashAttention is an optional acceleration library — when it's not present, PyTorch's built-in scaled-dot-product attention (SDPA) runs instead. Generation works normally.

**Why it shows up on Apple Silicon:** FlashAttention is an NVIDIA-only library and doesn't apply here at all. MLX has its own optimized attention kernels. Ignore the warning.

### Connection Timeout

**Symptoms:**
- Long loading times
- "Connection timeout" errors

**Solution:**
- Restart the app
- Check your firewall settings
- Ensure localhost is accessible

### TTS or STT Worker Not Reachable

By default the desktop app runs speech generation and transcription in 2 worker processes next to the main server: the TTS worker on port 17494 and the STT worker on port 17495. Settings → General shows both URLs.

**Symptoms:**
- A generation fails with `Worker at http://127.0.0.1:17494 is not reachable`
- Dictation or transcription fails with `Worker at http://127.0.0.1:17495 is not reachable`
- A streaming client (for example a LiveKit agent) gets HTTP 404 or a WebSocket close with code 1008 and the message `... runs on the worker at ..., not on main`

**Solutions:**

<details>
<summary>Worker Port Already in Use</summary>

Check which process holds the worker ports:

```bash
lsof -i :17494 -i :17495
```

Stop the other process, then restart the app (or Settings → GPU → restart the server).

</details>

<details>
<summary>Worker Crashed</summary>

Open Settings → Logs and look for lines that start with `[tts]` or `[stt]`. The app restarts a worker that exits on its own, at most 3 times in 60 seconds. After that it logs `not restarting it until the next server start`. Fix the cause shown in the log, then restart the app.

</details>

<details>
<summary>Streaming Client Connects to the Main Server</summary>

The main server does not run `/tts/ws`, `/tts/stream`, `/tts/warmup`, `/tts/render` or `/stt/ws` when it has workers. Point the client at the worker URL from the error message or from Settings → General: `http://127.0.0.1:17494` for TTS, `http://127.0.0.1:17495` for STT.

</details>

<details>
<summary>Run Everything in 1 Process</summary>

Turn off Settings → General → *Separate TTS and STT processes* and restart the app. The server then runs TTS and STT itself, as before, and all routes are on the main server URL.

</details>

## Generation Issues

### First Generation is Very Slow

**Symptoms:**
- First generation takes 2-5 minutes
- Progress indicator stuck at "Loading model..."

**Explanation:**
This is expected behavior. The first generation downloads the selected TTS engine's model and initializes it. Sizes range from 350 MB (Kokoro) to 8 GB (TADA 3B).

**Solution:**
- Wait for the initial download to complete (progress is shown in Settings → Models)
- Subsequent generations reuse the cached model and are much faster
- Check your internet connection
- For low-bandwidth setups, start with Kokoro (~350 MB) or LuxTTS (~300 MB)

### Poor Voice Quality

**Symptoms:**
- Robotic or unnatural voice
- Missing emotion or prosody
- Pronunciation errors

**Solutions:**

**1. Improve Voice Samples**

- Use 10-30 seconds of clear audio
- Avoid background noise
- Ensure consistent speaking tone
- Add multiple samples from the same speaker

**2. Match Speaking Style**

The generated voice will mimic the tone and style of your samples. If your sample is monotone, the generation will be too.

**3. Adjust Text Formatting**

- Use proper punctuation
- Add commas for natural pauses
- Capitalize proper nouns

### Generation Fails with "Out of Memory"

**Symptoms:**
- Generation crashes
- "RuntimeError: out of memory"

**Solutions:**

<details>
<summary>Free GPU Memory</summary>

Close other GPU-intensive applications:
- Games
- Video editors
- Multiple browser tabs with WebGL

Then restart Voicebox.

</details>

<details>
<summary>Use CPU Mode</summary>

If your GPU doesn't have enough VRAM (need 6GB+), use CPU mode:

Settings → Generation → Use CPU instead of GPU

> [!WARNING]
> CPU generation is 5-10x slower but uses system RAM instead of VRAM.

</details>

<details>
<summary>Reduce Batch Size</summary>

For long text, split it into smaller chunks instead of generating all at once.

</details>

### MLX "Failed to load the default metallib" (Apple Silicon)

**Symptoms:**
- Generation fails with "library not found" or "metallib" errors
- Server logs reference missing Metal shader libraries

**Solutions:**

<details>
<summary>Rebuild the Server Binary</summary>

```bash
just build-server
```

The build script bundles MLX Metal shader libraries on Apple Silicon automatically.

</details>

<details>
<summary>Reinstall MLX Dependencies</summary>

```bash
pip install -r backend/requirements-mlx.txt
```

</details>

<details>
<summary>Verify Backend Detection</summary>

Check Settings → Server Status. Should show **Backend: MLX** on Apple Silicon. If it shows **Backend: PYTORCH**, MLX isn't installed correctly.

</details>

## Audio Issues

### No Audio Playback

**Symptoms:**
- Generated audio won't play
- Playback button doesn't respond

**Solutions:**
- Check system audio settings
- Ensure audio output device is connected
- Try exporting and playing in a media player

### Crackling or Distorted Audio

**Symptoms:**
- Audio has static or distortion
- Clipping sounds

**Solutions:**
- Check if your input samples have distortion
- Reduce playback volume
- Re-generate with cleaner voice samples

## Development Issues

### Backend Won't Start in Dev Mode

**Symptoms:**
- `just dev-backend` or `just dev` fails
- Import errors or module not found

**Solutions:**

<details>
<summary>Python Version</summary>

Ensure Python 3.12:

```bash
python3.12 --version
```

Voicebox pins its venv to Python 3.12 because some dependencies have no 3.13
release yet. `just setup` recreates a venv that was built on the wrong version,
so re-running it is usually the fix.

If `just setup` can't find a usable 3.12, install one with
`uv python install 3.12` or `brew install python@3.12`.

</details>

<details>
<summary>Virtual Environment</summary>

Ensure venv is activated:

```bash
source backend/venv/bin/activate
```

You should see `(venv)` in your prompt.

</details>

<details>
<summary>Dependencies</summary>

Reinstall dependencies — easiest via `just`:

```bash
just setup
```

Or manually:

```bash
cd backend
pip install -r requirements.txt
pip install --no-deps chatterbox-tts
pip install --no-deps hume-tada
pip install git+https://github.com/QwenLM/Qwen3-TTS.git
```

</details>

### Tauri Build Fails

**Symptoms:**
- `bun run tauri build` fails
- Rust compilation errors

**Solutions:**

```bash
# Clean build artifacts
cd tauri/src-tauri
cargo clean

# Update Rust
rustup update

# Try building again
cd ../..
bun run tauri build
```

### OpenAPI Client Generation Fails

**Symptoms:**
- `./scripts/generate-api.sh` fails
- "Failed to fetch schema" error

**Solutions:**

**1. Ensure Backend is Running**

```bash
curl http://localhost:17593/openapi.json
```

Should return JSON. If not, start the backend.

**2. Check Port**

Ensure nothing else is using port 17593

**3. Regenerate Manually**

```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload --port 17593

# In another terminal
./scripts/generate-api.sh
```

## Database Issues

### "Database is locked" Error

**Symptoms:**
- Profile or generation operations fail
- SQLite lock errors

**Solutions:**
- Close all Voicebox instances
- Delete the lock file:
  ```bash
  rm ~/Library/Application\ Support/com.koval-yurko.voicebox-yk/data/voicebox.db-shm
  rm ~/Library/Application\ Support/com.koval-yurko.voicebox-yk/data/voicebox.db-wal
  ```

### Corrupted Database

**Symptoms:**
- App crashes on launch
- Data missing or corrupted

**Solutions:**

> [!WARNING]
> This will delete all your voice profiles and generation history. Export important profiles first if possible.

```bash
rm ~/Library/Application\ Support/com.koval-yurko.voicebox-yk/data/voicebox.db*
```

The wildcard also removes the `voicebox.db-wal` and `voicebox.db-shm` sidecar files that SQLite keeps next to the database in WAL mode, so the reset starts completely clean.

Restart the app to create a fresh database.

## Model Issues

### Model Download Fails

**Symptoms:**
- "Failed to download model" error
- Stuck at "Downloading..."

**Solutions:**
- Check your internet connection
- Check HuggingFace Hub status
- Try using a VPN if HuggingFace is blocked in your region
- Manually download via the HuggingFace CLI and place in the cache directory:

  ```bash
  pip install huggingface_hub
  huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-Base
  ```

### Wrong Model Version

**Symptoms:**
- Generation quality suddenly degraded
- Different voice output

**Solutions:**
Clear the model cache and re-download. Replace the `Qwen*` glob with the engine org prefix for other engines (`ResembleAI*` for Chatterbox, `HumeAI*` for TADA, `hexgrad*` for Kokoro, etc.) or use `DELETE /models/{name}` via the API.

```bash
rm -rf ~/.cache/huggingface/hub/models--Qwen*
```

## Performance Issues

### Slow Generation

**Symptoms:**
- Generation slower than expected
- GPU not being utilized

**Solutions:**

<details>
<summary>Apple Silicon: Confirm MLX Backend</summary>

Check Settings → Server Status. Should show **Backend: MLX** on Apple Silicon — MLX is 4–5× faster than PyTorch here. If it shows **Backend: PYTORCH**, reinstall MLX:

```bash
pip install -r backend/requirements-mlx.txt
```

GPU availability should read "Metal (Apple Silicon via MLX)".

</details>

### High Memory Usage

**Symptoms:**
- App uses excessive RAM
- System becomes sluggish

**Solutions:**
- Close unused voice profiles
- Clear generation history
- Restart the app periodically

## Update Issues

### "Update Check Failed"

**Solutions:**
- Confirm your internet connection — updates are fetched from GitHub releases.
- Ensure `github.com` is accessible and not blocked by a firewall or proxy.
- As a fallback, download the latest release from GitHub and install manually.

### "Invalid Signature" Error

**Solutions:**
- Re-download the installer — the signature may have been corrupted in transit.
- Verify the `.sig` file matches the installer; if it doesn't, file an issue.

## Remote Mode Issues

### Can't Connect to Remote Server

**Symptoms:**
- "Connection refused" error
- Remote server not found

**Solutions:**

**1. Check Server Status**

Ensure the remote server is running:

```bash
curl http://<server-ip>:17593/health
```

**2. Check Firewall**

Ensure port 17593 is open on the remote server:

On the server Mac, check **System Settings → Network → Firewall**.

**3. Verify Network**

- Ensure both machines are on the same network (for local servers)
- Use IP address instead of hostname
- Try pinging the server: `ping <server-ip>`

## Still Having Issues?

If you're still experiencing problems:

1. **Check GitHub Issues:** [github.com/koval-yurko/voicebox/issues](https://github.com/koval-yurko/voicebox/issues)
2. **Open a New Issue:** Provide:
   - macOS version
   - Voicebox version
   - Steps to reproduce
   - Error messages or logs

## Diagnostic Information

When reporting issues, include this information:

```bash
# Voicebox version
# Check Help → About in the app

# macOS version
sw_vers

# Python version (for dev issues)
python --version

```
