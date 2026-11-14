# Voicebox YK verification map

This directory is the maintained source for verifying the user-facing behavior of the Voicebox YK backend. Read the index before you drive the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Start a disposable backend with `$V up` (`V=.claude/skills/verify-voicebox/scripts/vb-verify`). It listens on `http://127.0.0.1:17693` with an empty data dir.
- Run `$V doctor` and require every line to be `ok`.
- Cached models (in `~/.cache/huggingface/hub`, checked 2026-11-14): STT `whisper-base`, `whisper-turbo`, Qwen3-ASR 0.6B/1.7B; TTS `qwen` 0.6B and 1.7B (MLX). The recipes use `whisper-base` and `qwen` `0.6B` because they load fast.
- Never drive an instance that this run did not start. Never call ports 17493, 17494, 17495 or 17593.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Send every request through `$V curl <name> <path> ...` so it is saved as evidence. Use a new `<name>` for each step. Prefix the path with `tts:` or `stt:` to reach a worker in split mode.
- Send WebSocket and MCP traffic through `$V client <stt-ws|tts-ws|mcp> <name> ...`. It saves every server message to `<name>.events.jsonl`.
- Make test speech with `$V wav <name> "<text>"`. Do not use audio files from the user's data.
- Treat every command as literal. Keep quoted names and flags unchanged.
- Act as an external client only with `-H 'Cf-Ray: verify'` and a key you made in the disposable data dir.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the response to the action.
- A mutation needs a second read-only view (a list or detail endpoint).
- Generated speech needs a content check: transcribe it and check the text.
- Record the run ID, the feature ID and the entry point with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph that describes the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with vb-verify` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and an observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Transcribe audio](./transcribe.md) covers batch speech-to-text with a chosen model and language. Proven 2026-11-14 in single and split mode.
- [Voice profiles](./voice-profiles.md) covers creating a cloned voice, adding a sample, listing and deleting. Proven 2026-11-14.
- [Generate speech](./generate-speech.md) covers text-to-speech in a cloned voice, the status stream, the audio file and history, and `POST /speak`. Proven 2026-11-14 in split mode.
- [External access](./external-access.md) covers the API key gate for tunnel clients. Proven 2026-11-14.
- [Streaming speech-to-text](./stt-stream.md) covers `WS /stt/ws` on the STT worker. Proven 2026-11-14 in split mode.
- [Streaming text-to-speech](./tts-stream.md) covers `WS /tts/ws` and `POST /tts/stream` on the TTS worker. Proven 2026-11-14 in split mode.
- [MCP tools](./mcp.md) covers the 4 tools at `/mcp/`. Proven 2026-11-14 in split mode.

## Not mapped yet

- Captures (dictation), stories, effects, channels, the OpenAI-compatible `/v1/audio/speech`.
- The desktop UI (Tauri) and the browser UI (`web/`).
