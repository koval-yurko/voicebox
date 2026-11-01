<p align="center">
  <img src=".github/assets/icon-dark.webp" alt="Voicebox YK" width="120" height="120" />
</p>

<h1 align="center">Voicebox YK</h1>

<p align="center">
  A local AI voice studio for macOS Apple Silicon: voice cloning, text-to-speech, dictation and speech-to-text.
</p>

<p align="center">
  <a href="https://github.com/koval-yurko/voicebox/releases/latest">
    <img src="https://img.shields.io/github/v/release/koval-yurko/voicebox?style=flat" alt="Release" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/koval-yurko/voicebox?style=flat" alt="License" />
  </a>
</p>

<p align="center">
  Based on <a href="https://github.com/jamiepine/voicebox">Voicebox</a> by Jamie Pine (MIT).
</p>

<p align="center">
  <img src=".github/assets/screenshot.webp" alt="Voicebox YK screenshot" width="800" />
</p>

---

## What is different in this fork

- **macOS Apple Silicon only.** TTS and STT run locally with MLX/Metal.
- **Separate TTS and STT processes.** The app runs a main server plus a TTS worker (port 17494) and an STT worker (port 17495), so a transcript does not wait for a long generation. You can turn this off in Settings → General.
- **No built-in LLM.** Personalities and dictation refinement use an OpenAI-compatible LLM endpoint that you set in Settings → Captures (for example LM Studio, Ollama or `mlx_lm.server`).
- **Streaming voice I/O.** Streaming STT over `WS /stt/ws` and streaming TTS over `WS /tts/ws` and `POST /tts/stream`, served by the workers.
- **External API access with API keys.** Requests from outside the Mac need a key (`Authorization: Bearer <key>` or `X-API-Key`). Manage keys with `scripts/voicebox_api_key.py`.
- **LiveKit Agents plugin.** `VoiceboxSTT` and `VoiceboxTTS` in [`integrations/livekit`](integrations/livekit/README.md).
- **Runs next to the original Voicebox.** It has its own bundle id (`com.koval-yurko.voicebox-yk`), its own server port (`17593`) and its own MCP server name (`voicebox-yk`).

## Download

Get the latest DMG from [Releases](https://github.com/koval-yurko/voicebox/releases/latest). There is a build for Apple Silicon (M1 or later) only.

The app is not signed with an Apple certificate. After you install it, run:

```bash
xattr -dr com.apple.quarantine "/Applications/Voicebox YK.app"
```

Or right-click the app and select **Open** the first time.

## Features

- **Voice cloning and preset voices**: zero-shot cloning from a short sample, or preset voices.
- **Multiple TTS engines**: Qwen3-TTS, Qwen CustomVoice, Qwen VoiceDesign, LuxTTS, Chatterbox Multilingual, Chatterbox Turbo, HumeAI TADA and Kokoro.
- **Dictation**: a global hotkey with push-to-talk and toggle modes, and auto-paste into the focused field.
- **Speech-to-text**: Whisper models, with an archive of captures (audio and transcript).
- **Voice personalities**: a persona for each voice profile, used by your LLM endpoint to compose or rewrite text.
- **Stories editor**: a multi-track timeline for conversations and narration.
- **Effects**: pitch shift, reverb, delay, chorus, compression and filters.
- **MCP server**: agents such as Claude Code can speak and transcribe through Voicebox YK.

## API and MCP

The main server runs on `http://127.0.0.1:17593`. The Swagger UI is at `http://127.0.0.1:17593/docs`. Streaming clients connect to the TTS worker (`17494`) and the STT worker (`17495`) directly.

```bash
# Speak text in a voice profile
curl -X POST http://127.0.0.1:17593/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Deploy complete.", "profile": "Morgan"}'

# Transcribe an audio file
curl -X POST http://127.0.0.1:17593/transcribe -F "audio=@recording.wav"

# List voice profiles
curl http://127.0.0.1:17593/profiles
```

The MCP server is at `http://127.0.0.1:17593/mcp`. It has 4 tools: `voicebox_speak`, `voicebox_transcribe`, `voicebox_list_captures` and `voicebox_list_profiles`. See the [API](https://github.com/koval-yurko/voicebox/wiki/API) and [MCP Server](https://github.com/koval-yurko/voicebox/wiki/MCP-Server) wiki pages.

## Development

```bash
git clone git@github.com:koval-yurko/voicebox.git
cd voicebox

just setup   # creates the Python venv and installs all dependencies
just dev     # starts the backend and the desktop app
```

You need [Bun](https://bun.sh), [Rust](https://rustup.rs), [just](https://github.com/casey/just), [uv](https://docs.astral.sh/uv/) and Xcode. Run `just --list` to see all commands. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Developer Setup](https://github.com/koval-yurko/voicebox/wiki/Developer-Setup) wiki page.

To add a TTS engine, read [`docs-internal/tts-engines.mdx`](docs-internal/tts-engines.mdx). The [add-tts-engine agent skill](.agents/skills/add-tts-engine/SKILL.md) uses the same guide.

## Documentation

All documentation is in the [wiki](https://github.com/koval-yurko/voicebox/wiki). The page sources are in [`docs/`](docs/). Edit them there: a push to `main` publishes them to the wiki, and edits made in the wiki itself are overwritten.

## License

MIT. See [LICENSE](LICENSE). Use voice cloning responsibly: see [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).
