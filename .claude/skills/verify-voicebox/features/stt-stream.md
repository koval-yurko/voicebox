# Streaming speech-to-text

A live client (the LiveKit agent, the dictation pill) sends microphone audio over a WebSocket and gets text events back while it speaks. In split mode this runs on the STT worker, not on main.

## Sub-features

- `stt-ws-ready` the `start` message opens a session and the server answers `ready` with the model and its capabilities.
- `stt-ws-final` PCM16 frames plus `end` give a `final` event with the spoken text, then `end`.
- `stt-ws-main-refuses` main refuses `/stt/ws` in split mode and names the worker URL.

## How to get to it (user POV)

- LiveKit plugin: `integrations/livekit/voicebox_livekit/stt.py` connects to `ws://<stt worker>/stt/ws` (through the tunnel: `stt.kovalchuk.win`).
- WebSocket `WS /stt/ws`: send `{"type":"start","model":...,"language":...,"sample_rate":16000,"vad":...}`, then binary PCM16 little-endian mono frames, then `{"type":"end"}`.
- Other client messages: `end_segment` (endpoint when `vad` is false), `cancel_segment` (drop the open segment).

## Driving it with vb-verify

Preconditions:

- Split mode: `$V up --split`, and `$V doctor` is all `ok` (`stt` on 17695).
- `whisper-base` is cached.

- **Make speech.** Record a known sentence. Run `W=$($V wav stt-in "The quick brown fox jumps over the lazy dog.")`.
- **Stream it.** Send it in 20 ms frames at real-time speed, then `end`. Run `$V client stt-ws stt-ws "$W" whisper-base`. The client goes to the STT worker port. Output, also in `stt-ws.events.jsonl`: a `ready` event with `"model": "whisper-base"`, a `final` event whose `text` starts with `The quick brown fox jumps over the lazy dog`, then `{"type": "end"}`.
- **Main refuses.** Point a WebSocket client at main (`ws://127.0.0.1:17693/stt/ws`). The server accepts the upgrade, then closes with the reason `/stt/ws runs on the worker at http://127.0.0.1:17695, not on main.`
- **Proof.** Keep `stt-in.wav`, `stt-ws.events.jsonl` and `logs/stt.log`.

## Gotchas

- The client sends `"vad": false`, so the segment ends only at `end`. With `"vad": true` the server finds speech ends itself and can send several `final` events.
- `whisper-base` has no native streaming (`"native_streaming": false`). It sends only `final` events, no `interim`. Use `voxtral-realtime-4B` to test interim text. It is cached but large.
- An uncached model gives an `error` event that says to download it first. It does not start a download.
- `$V client stt-ws` needs a 16 kHz mono PCM16 WAV. `$V wav` makes exactly that.
- The installed app's STT worker is on 17495. Never stream to it.
- Proven on 2026-11-14 in split mode (run `proof-stream-20261114`): `ready`, `final` with the correct text, `end` in about 3 s for a 2.8 s clip. The main refusal was seen on a raw upgrade request.
