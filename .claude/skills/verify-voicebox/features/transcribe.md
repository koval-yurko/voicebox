# Transcribe audio

A user sends a recorded audio file and gets back its text and duration. They can choose the STT model and the language. The desktop app, the LiveKit plugin (batch mode) and the MCP tool `voicebox_transcribe` use this path.

## Sub-features

- `stt-batch` returns the text of a WAV file with the chosen model.
- `stt-language` uses the given language instead of auto-detection.
- `stt-model-download` returns HTTP 202 and starts a download when the chosen model is not cached.
- `stt-bad-model` refuses an unknown model name.

## How to get to it (user POV)

- Desktop app: the Audio tab, transcribe a file.
- HTTP: `POST /transcribe` with multipart fields `file`, optional `model`, optional `language`.
- MCP: the `voicebox_transcribe` tool (not driven by this recipe).

## Driving it with vb-verify

Preconditions:

- `$V doctor` is all `ok`.
- `whisper-base` is cached (`ls ~/.cache/huggingface/hub | grep whisper-base`).

- **Make speech.** Record a known sentence. Run `W=$($V wav hello "The quick brown fox jumps over the lazy dog.")`. `$W` is a WAV path in the evidence dir.
- **Transcribe.** Send it with a model and language. Run `$V curl transcribe /transcribe -F "file=@$W" -F model=whisper-base -F language=en`. HTTP 200, and `text` starts with `The quick brown fox jumps over the lazy dog`. `duration` is about 2.8.
- **Unknown model.** Send a model name that does not exist. Run `$V curl transcribe-bad /transcribe -F "file=@$W" -F model=no-such-model`. HTTP 400, and `detail` names the model and lists the valid ones.
- **Proof.** Keep `hello.wav`, `transcribe.cmd`, `transcribe.out`, `transcribe.code` and `logs/all.log`. The log shows the model load and the request.

## Gotchas

- The first call loads the model, so it takes some seconds longer than the next calls.
- An uncached model returns HTTP 202 with `model_name` and starts a download into the shared Hugging Face cache. Do not pick an uncached model only to test this. If you must, pick the smallest one and tell the user.
- `whisper-base` can add words or dots at the end of very short clips. Check that the text starts with the expected words. Do not check for an exact match.
- Proven end to end on 2026-11-14 in both modes (runs `proof-20261114` and `proof-split-20261114`). In split mode the request shows in `logs/stt.log`.
- `say` voices and speed change between macOS versions. Use plain, common words.
