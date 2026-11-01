_Voicebox is the open-source, local-first AI voice studio — a free alternative to ElevenLabs and WisprFlow, running entirely on your machine._

## What is Voicebox?

Voicebox is the **open-source, local-first AI voice studio**. It closes the
voice I/O loop in both directions on one machine, with no cloud and no
accounts:

- **Humans talk** — hold a chord anywhere on your machine and your
  dictation lands as clean text in whatever text field you had focused
- **Agents talk back** — any MCP-aware agent can call Voicebox to speak in
  one of your cloned voices
- **Voices speak for themselves** — voice profiles can carry a personality
  that composes fresh lines or rewrites text before it's spoken

It's the free, local alternative to both ElevenLabs (voice cloning and TTS)
and WisprFlow (voice dictation for agents and power users) — covering both
sides of the same loop in one app, with a single model directory shared
between input and output.

## What's in the app

- **Dictation** — global hotkey, push-to-talk and toggle modes, auto-paste
  into the focused field on macOS (see [Dictation](Dictation))
- **Captures tab** — paired audio + transcript archive, retranscribe,
  refine, play-as-voice, promote-to-sample (see [Captures](Captures))
- **Voice cloning** — 5 cloning engines covering 23 languages. Zero-shot
  cloning from a reference sample (see [Voice Cloning](Voice-Cloning))
- **Preset voices** — 50+ curated voices via Kokoro and Qwen CustomVoice
  for when you don't want to clone (see [Preset Voices](Preset-Voices))
- **Voice personalities** — optional free-form personality on any profile
  plus a compose button and persona-rewrite toggle powered by your LLM endpoint (see
  [Voice Personalities](Voice-Personalities))
- **Post-processing effects** — pitch shift, reverb, delay, chorus,
  compression, filters (Spotify's Pedalboard)
- **Expressive speech** — paralinguistic tags like `[laugh]` and `[sigh]`
  via Chatterbox Turbo; natural-language delivery control via Qwen CustomVoice
- **Unlimited length** — auto-chunking with crossfade for long scripts
- **Stories editor** — multi-track timeline for conversations and podcasts
- **API-first** — REST + WebSocket API; MCP server for agent integrations
- **Runs on macOS Apple Silicon** — local inference with MLX/Metal

## TTS Engines

Eight engines with different strengths, switchable per-generation:

| Engine | Profile Type | Languages | Strengths |
|--------|--------------|-----------|-----------|
| **Qwen3-TTS** (0.6B / 1.7B) | Cloned | 10 | High-quality multilingual cloning |
| **Qwen CustomVoice** (0.6B / 1.7B) | Preset (9 voices) | 10 | Natural-language delivery control (tone, emotion, pace) |
| **Qwen VoiceDesign** (1.7B) | Designed | 10 | Builds a voice from a written description — no reference audio or preset |
| **LuxTTS** | Cloned | English | Lightweight (~1GB VRAM), 48kHz output, 150x realtime on CPU |
| **Chatterbox Multilingual** | Cloned | 23 | Broadest language coverage |
| **Chatterbox Turbo** | Cloned | English | Fast 350M model with paralinguistic emotion/sound tags |
| **TADA** (1B / 3B) | Cloned | 10 | HumeAI speech-language model — 700s+ coherent audio |
| **Kokoro** | Preset (50 voices) | 9 | 82M parameters, CPU realtime, lowest VRAM of any engine |

## STT and LLM

Voicebox runs speech recognition locally, shared between dictation and the
Captures tab. It has no built-in LLM: transcript refinement and per-profile
personality modes use an LLM endpoint you set.

| Layer | Models |
|---|---|
| **STT** | Whisper Base / Small / Medium / Large / Turbo (PyTorch or MLX) |
| **LLM** | Your OpenAI-compatible endpoint — LM Studio, Ollama, `mlx_lm.server`, llama.cpp, vLLM or the OpenAI API (Settings → Captures) |

Without an endpoint, dictation keeps the raw transcript and the personality
controls are disabled. Run the LLM server on the same machine to keep
everything local.

## GPU Support

| Platform | Backend | Notes |
|----------|---------|-------|
| macOS (Apple Silicon) | MLX (Metal) | 4-5x faster via Neural Engine |

## Use Cases

- **Dictation for humans and agents** — speak instead of type, in any app
- **Agent voice output** — any MCP-aware agent can speak in a cloned voice
- **Game development** — generate dynamic dialogue for characters
- **Content creation** — podcasts, video voiceovers, audiobooks
- **Accessibility** — speech-to-text for any field, TTS with a voice you own
- **Voice assistants** — custom voice interfaces without a cloud bill
- **Production pipelines** — automate voice workflows via the REST API

## Tech Stack

| Layer | Technology |
|-------|------------|
| Desktop App | Tauri (Rust) |
| Frontend | React, TypeScript, Tailwind CSS |
| State | Zustand, React Query |
| Backend | FastAPI (Python) |
| TTS Engines | Qwen3-TTS, Qwen CustomVoice, LuxTTS, Chatterbox, Chatterbox Turbo, TADA, Kokoro |
| STT | Whisper / Whisper Turbo (PyTorch or MLX) |
| LLM | None built in; optional OpenAI-compatible endpoint |
| Effects | Pedalboard (Spotify) |
| Inference | MLX (Apple Silicon) / PyTorch (MPS/CPU) |
| Database | SQLite |
| Audio | WaveSurfer.js, librosa |
