# Generate speech

A user types text, picks a voice profile and an engine, and gets an audio file that speaks the text in that voice. The job runs in the background. The user sees its status, then plays or downloads the audio from history.

## Sub-features

- `gen-start` queues a generation and returns `status: "generating"` at once.
- `gen-status` streams status over SSE until `completed` or `failed`.
- `gen-audio` serves the finished WAV.
- `gen-history` lists the generation with its duration and engine.
- `gen-content` the audio speaks the requested text.
- `gen-speak` `POST /speak` queues the same kind of job with a profile name instead of an ID.

## How to get to it (user POV)

- Desktop app: the generate box under the main editor.
- HTTP: `POST /generate`, then `GET /generate/{id}/status` (SSE), `GET /history/{id}`, `GET /audio/{id}`.
- HTTP for agents: `POST /speak` with `text` and `profile` (name or ID). It returns the same generation shape.
- MCP: `voicebox_speak` (not driven by this recipe).

## Driving it with vb-verify

Preconditions:

- `$V doctor` is all `ok`.
- A cloned profile with one sample exists. Follow [voice profiles](./voice-profiles.md) steps `Create` and `Add sample`, and keep its ID as `P`.
- The `qwen` 0.6B MLX model is cached (`ls ~/.cache/huggingface/hub | grep Qwen3-TTS-12Hz-0.6B`).

- **Start.** Ask for speech. Run `$V curl generate /generate -H 'Content-Type: application/json' -d "{\"profile_id\":\"$P\",\"text\":\"Verification run complete.\",\"engine\":\"qwen\",\"model_size\":\"0.6B\"}"`. HTTP 200 and `status` is `generating`. Keep the `id` as `G`.
- **Wait.** Follow the status stream. Run `curl -sN --max-time 120 http://127.0.0.1:17693/generate/$G/status | tee .verify/evidence/<run-id>/gen-status.sse`. The last event has `"status": "completed"` and a `duration` above 0.
- **History.** Read it back. Run `$V curl gen-history /history/$G`. `status` is `completed`, `audio_path` is `generations/<G>.wav`, `profile_name` is `Verify Voice`.
- **Audio.** Download the file. Run `curl -s -o .verify/evidence/<run-id>/generated.wav -w '%{http_code} %{size_download}\n' http://127.0.0.1:17693/audio/$G`. Output is `200` and a size above 0.
- **Content.** Transcribe the result. Run `$V curl gen-roundtrip /transcribe -F "file=@.verify/evidence/<run-id>/generated.wav" -F model=whisper-base -F language=en`. `text` starts with `Verification run complete`.
- **Speak.** Ask by profile name. Run `$V curl speak /speak -H 'Content-Type: application/json' -d '{"text":"Speak endpoint works.","profile":"Verify Voice","engine":"qwen"}'`. HTTP 200 and `status` is `generating`. Keep the `id` as `S`, then repeat **Wait**, **Audio** and **Content** with `S`. The text starts with `Speak endpoint works`.

## Gotchas

- The first generation also loads the TTS model, so it is slower than the next ones.
- The status stream sends an event about once a second and closes by itself after `completed` or `failed`. Still pass `--max-time`: a job that hangs keeps the stream open.
- Start the stream right after `POST /generate`. A finished job gives only one `completed` event.
- `model_size` accepts only `1.7B`, `0.6B`, `1B`, `3B`, and not every engine has every size.
- Without `engine` the server uses the profile default or the saved setting. Pass it to make the run repeatable.
- `POST /speak` has no `model_size` field. It always uses the engine default (`1.7B` for `qwen`), so it is slower than the 0.6B recipe and needs the 1.7B model in the cache.
- The stored generation from `/speak` has `source: "manual"`, also in the status stream and history, although the code publishes a `speak-start` event with `source: "rest"`. Do not assert `rest` on the row. See [MCP tools](./mcp.md) for the same mismatch.
- Short outputs make `whisper-base` add junk words at the end (seen: foreign words, repeated phrases). Check the start of the text only.
- Proven end to end on 2026-11-14 in split mode (run `proof-stream-20261114`). `POST /generate` with `qwen` 0.6B: live stream `generating` 3 times then `completed`, 3 s in total, 2.24 s of audio, correct transcript. `POST /speak`: completed with 1.84 s of audio, transcript `Speak End Point Works.`
