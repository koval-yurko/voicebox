# MCP tools

AI agents (Claude Code, other MCP clients) use Voicebox through MCP at `/mcp/` on main: they list voices, speak text in a voice, list captures and transcribe audio. The repo's `.mcp.json` points Claude Code at the installed app (`http://127.0.0.1:17593/mcp`), so verification must use its own client.

## Sub-features

- `mcp-tools` the server lists exactly `voicebox_list_captures`, `voicebox_list_profiles`, `voicebox_speak`, `voicebox_transcribe`.
- `mcp-list-profiles` returns the profiles of this data dir.
- `mcp-speak` queues a generation and returns `generation_id`, `status`, `profile`, `source: "mcp"`, `poll_url`.
- `mcp-transcribe` transcribes a local file (`audio_path`, loopback only) or `audio_base64`.
- `mcp-list-captures` returns the captures list (empty on a fresh data dir).

## How to get to it (user POV)

- An MCP client configured with `{"type": "http", "url": "http://<host>:<port>/mcp"}`, as in `.mcp.json`.
- The desktop app's Settings, MCP page, shows per-client voice bindings (`/mcp/bindings`).

## Driving it with vb-verify

Preconditions:

- `$V doctor` is all `ok` (single or split mode; MCP is on main).
- A cloned profile `Verify Voice` with one sample exists. Follow [voice profiles](./voice-profiles.md) steps `Create` and `Add sample`.
- `W` is a WAV from `$V wav`, as an absolute path.

- **List tools and profiles.** Run `$V client mcp mcp-list-profiles voicebox_list_profiles '{}'`. The first output line lists the 4 tools. The second has `Verify Voice` with `"voice_type": "cloned"`.
- **Speak.** Run `$V client mcp mcp-speak voicebox_speak '{"text":"MCP speak works.","profile":"Verify Voice","engine":"qwen","model_size":"0.6B"}'`. The result has `"status": "generating"`, `"source": "mcp"` and `poll_url`. Keep `generation_id` as `M`.
- **Speak finishes.** Run `curl -sN --max-time 120 http://127.0.0.1:17693/generate/$M/status | tee .verify/evidence/<run-id>/mcp-speak-status.sse`. The last event is `completed`. Download `GET /audio/$M` and transcribe it as in [generate speech](./generate-speech.md). The text starts with `MCP speak works`.
- **Transcribe.** Run `$V client mcp mcp-transcribe voicebox_transcribe "{\"audio_path\":\"$W\",\"model\":\"whisper-base\",\"language\":\"en\"}"`. `result.text` starts with the spoken sentence and `result.model` is `whisper-base`.
- **List captures.** Run `$V client mcp mcp-list-captures voicebox_list_captures '{}'`. `{"captures": [], "total": 0}` on a fresh run.
- **Proof.** Keep every `mcp-*.events.jsonl`, `mcp-speak-status.sse`, `mcp-speak.wav` and its transcript.

## Gotchas

- Do not use this session's `mcp__voicebox__*` tools for verification. They reach the installed app on 17593 and write to the user's real history.
- `voicebox_speak` says audio plays on the user's speakers. The desktop app does that, so on a disposable backend nothing plays. The generation and its audio file are the proof.
- The tool result says `source: "mcp"`, but the stored generation (SSE stream, history) says `source: "manual"`. `POST /speak` has the same mismatch. Seen on 2026-11-14. Report it if your change touches it, and do not assert `mcp` on the stored row.
- `audio_path` works only for loopback callers. A tunnel client must send `audio_base64`.
- Pass exactly one of `audio_path` and `audio_base64`. Both or neither is an error.
- Proven on 2026-11-14 in split mode (run `proof-stream-20261114`): all 4 tools answered, speak completed with a correct transcript, transcribe returned the correct text.
