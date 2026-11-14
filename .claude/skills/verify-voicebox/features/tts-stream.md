# Streaming text-to-speech

A live client (the LiveKit agent) sends text and gets audio back while the speech is still being made. There are 2 paths: a WebSocket for incremental text (`/tts/ws`) and one HTTP request with a streamed body (`/tts/stream`). In split mode both run on the TTS worker, not on main. Streams do not create history rows.

## Sub-features

- `tts-ws-segments` `/tts/ws` splits buffered text into sentences and sends `segment_start`, PCM16 audio, `segment_end` for each.
- `tts-ws-latency` each `segment_end` reports `first_chunk_ms` and `audio_ms`.
- `tts-stream-wav` `POST /tts/stream` with `"format": "wav"` returns a WAV body as it is made.
- `tts-stream-main-refuses` main answers `/tts/stream` and `/tts/ws` with the worker URL in split mode.

## How to get to it (user POV)

- LiveKit plugin: `integrations/livekit/voicebox_livekit/tts.py` (through the tunnel: `tts.kovalchuk.win`).
- WebSocket `WS /tts/ws`: `{"type":"start","profile_id":...,"engine":...,"model_size":...}`, then `{"type":"text","text":...}` frames, then `flush`, `cancel` or `end`.
- HTTP `POST /tts/stream` with `profile_id`, `text`, optional `engine`, `model_size`, `format` (`pcm` or `wav`).

## Driving it with vb-verify

Preconditions:

- Split mode: `$V up --split`, and `$V doctor` is all `ok` (`tts` on 17694).
- A cloned profile with one sample exists. Follow [voice profiles](./voice-profiles.md) steps `Create` and `Add sample`, and keep its ID as `P`.
- The `qwen` 0.6B MLX model is cached.

- **WebSocket.** Stream 2 sentences. Run `$V client tts-ws tts-ws "$P" "The stream is live. This is the second sentence."`. The client goes to the TTS worker port. Output, also in `tts-ws.events.jsonl`: `ready` (`"sample_rate": 24000`, `"format": "s16le"`), `segment_start` id 1 `The stream is live.`, `segment_end` id 1, the same for id 2, then `end`. The audio is in `tts-ws.wav`.
- **WebSocket content.** Transcribe it. Run `$V curl tts-ws-roundtrip /transcribe -F "file=@.verify/evidence/<run-id>/tts-ws.wav" -F model=whisper-base -F language=en`. `text` starts with `The stream is live. This is the second sentence.`
- **HTTP stream.** Ask the worker for a WAV. Run `curl -s -o .verify/evidence/<run-id>/tts-stream.wav -w '%{http_code} %{size_download} %{time_starttransfer}\n' -X POST http://127.0.0.1:17694/tts/stream -H 'Content-Type: application/json' -d "{\"profile_id\":\"$P\",\"text\":\"Streaming over HTTP works.\",\"engine\":\"qwen\",\"model_size\":\"0.6B\",\"format\":\"wav\"}"`. Output is `200`, a size above 0 and a first byte well under the total time. `file` on the result says `WAVE audio, Microsoft PCM, 16 bit, mono 24000 Hz`.
- **HTTP content.** Run `$V curl tts-stream-roundtrip /transcribe -F "file=@.verify/evidence/<run-id>/tts-stream.wav" -F model=whisper-base -F language=en`. `text` starts with `Streaming over HTTP works`.
- **Main refuses.** Run `$V curl tts-stream-on-main /tts/stream -H 'Content-Type: application/json' -d "{\"profile_id\":\"$P\",\"text\":\"x\"}"`. HTTP 404 and `detail` is `/tts/stream runs on the worker at http://127.0.0.1:17694, not on main. ...`.

## Gotchas

- `$V curl tts:/tts/stream ...` also reaches the worker. The recipe uses plain `curl` for `/tts/stream` because it needs `-w` timing.
- `$V client tts-ws` always uses `engine` `qwen` and `model_size` `0.6B`. Change `vb-client.py` to test another engine.
- The default `format` is `pcm` (raw s16le, no header). Use `wav` when you want to play or transcribe the file.
- `whisper-base` adds junk words after short TTS clips (for example `No No No`, repeated URLs). Check only the start of the text.
- Streams do not write history: `backend/routes/tts_stream.py` creates no generation row (read in the code, not checked in a run). Proof is the saved audio plus its transcript, not `GET /history`.
- The installed app's TTS worker is on 17494. Never stream to it.
- Proven on 2026-11-14 in split mode (run `proof-stream-20261114`): `/tts/ws` gave 2 segments with `first_chunk_ms` 142 and 94, 3.6 s of audio, correct transcript. `/tts/stream` gave its first byte at 0.12 s and a 1.3 s total for 2.4 s of audio, correct transcript. Main answered HTTP 404 with the worker URL.
