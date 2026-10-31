# STT investigation: current state, models, streaming and LiveKit

Date: 2026-10-28. Base commit: `3abe9e1`.

## 1. Summary

- Voicebox has 1 STT engine: OpenAI Whisper, in 5 sizes.
- STT is batch only. The client sends a complete audio file and gets 1 text string back.
- There is no STT streaming: no WebSocket, no partial results, no VAD, no word timestamps.
- The streaming endpoint `/transcribe/stream` is planned in `docs/plans/VOICE_IO.md`, but it is not built.
- LiveKit Agents can use Voicebox STT today only through a small custom plugin plus the LiveKit `StreamAdapter` and Silero VAD.
- The external API key gate allows only `/tts/*`. A LiveKit worker that connects through the tunnel cannot reach `/transcribe` now.

## 2. What we have

### 2.1 Engine and models

The registry is in `backend/backends/__init__.py`.

| Size key | HF repo | Notes |
|---|---|---|
| `base` | `openai/whisper-base` | Default size |
| `small` | `openai/whisper-small` | |
| `medium` | `openai/whisper-medium` | |
| `large` | `openai/whisper-large-v3` | |
| `turbo` | `openai/whisper-large-v3-turbo` | Best speed/quality balance |

- `WHISPER_HF_REPOS` (`backend/backends/__init__.py:39`) maps a size key to a repo.
- `_get_whisper_configs()` (`backend/backends/__init__.py:370`) registers the same 5 models as `ModelConfig` entries with `engine="whisper"`. The model management UI uses these entries for download, status and unload.
- The `STTBackend` protocol (`backend/backends/__init__.py:136`) has 4 methods: `load_model`, `transcribe(audio_path, language, model_size) -> str`, `unload_model`, `is_loaded`.
- `get_stt_backend()` (`backend/backends/__init__.py:718`) returns 1 global instance. It selects the implementation by platform:
  - Apple Silicon: `MLXSTTBackend` (`backend/backends/mlx_backend.py:371`). It uses `mlx_audio.stt.load(...)` and `model.generate(path)`.
  - All other platforms: `PyTorchSTTBackend` (`backend/backends/pytorch_backend.py:248`). It uses `transformers` `WhisperForConditionalGeneration` on CUDA, XPU, DirectML, MPS or CPU.
- Only 1 Whisper model is in memory at a time. A request for a different size unloads the current model and loads the new one.

### 2.2 Entry points

| Entry point | File | What it does |
|---|---|---|
| `POST /transcribe` | `backend/routes/transcription.py:19` | Multipart `file`, optional `language` and `model`. Returns `{text, duration}`. Returns HTTP 202 and starts a download when the model is not cached. |
| `POST /captures` | `backend/routes/captures.py:24` | Saves the audio, transcribes it, can refine the text with the local LLM. Used by dictation and the Captures tab. |
| `POST /captures/{id}/retranscribe` | `backend/routes/captures.py:203` | Transcribes a saved capture again, with a different model if needed. |
| MCP tool `voicebox.transcribe` | `backend/mcp_server/tools.py:108` | Base64 audio or a local file path (loopback only). |
| Frontend | `app/src/lib/hooks/useTranscription.ts`, `useCaptureRecordingSession.ts`, `DictateWindow.tsx` | Records the full clip, then sends it in 1 request. |

### 2.3 Limits and problems found

1. **No streaming.** Every path writes the audio to a temp file and transcribes the whole file (`backend/routes/transcription.py:26`).
2. **Possible 30 s cut on the PyTorch path.** `PyTorchSTTBackend.transcribe` calls `WhisperProcessor(audio)` with default arguments. The default feature extractor pads or cuts the input to 30 s. Thus clips longer than 30 s on Windows, Linux and Docker probably lose the end of the text. The MLX path handles long audio. Verify this with a 60 s clip before you fix it.
3. **STT does not use `inference_lock`.** TTS takes `inference_lock` (`backend/services/task_queue.py:32`), but STT does not. STT and TTS can run on the GPU at the same time. In a voice agent, this can slow down the first TTS chunk.
4. **Language validation is not consistent.** `TranscriptionRequest` (`backend/models.py:171`) allows only 10 languages, but the route does not use this model. Whisper itself supports about 99 languages.
5. **No timestamps, no confidence, no language detection result.** The response has only `text` and `duration`.
6. **External access.** `EXTERNAL_PATH_PREFIXES = ("/tts/",)` in `backend/utils/api_keys.py:38`. External clients (the LiveKit worker through Cloudflare Tunnel) get HTTP 403 on `/transcribe`.

## 3. Can we add more models?

Yes. The design supports it, but the registry is Whisper-specific today.

### 3.1 More Whisper variants (small change)

Add a key to `WHISPER_HF_REPOS` and a `ModelConfig` to `_get_whisper_configs()`. Both backends load any HF Whisper repo. Also update the `model` regex in `backend/models.py:175` and the frontend model list. Candidates:

- `distil-whisper/distil-large-v3.5` — about 1.5x faster than large-v3, English only.
- `openai/whisper-tiny` — for CPU-only machines.
- On MLX, `mlx-community/whisper-large-v3-turbo` (pre-converted weights, faster load).

### 3.2 New STT engines (medium change)

To add a non-Whisper engine, do these steps:

1. Add an `STT_ENGINES` dict, like `TTS_ENGINES` (`backend/backends/__init__.py:210`).
2. Change `get_stt_backend()` to take an engine name and keep 1 instance per engine, like `_tts_backends`.
3. Write a new backend class that implements `STTBackend`.
4. Register its `ModelConfig` entries with the new `engine` value.
5. Change `/transcribe`, captures and MCP to accept an engine + model, not only a Whisper size.

Candidates (the plan in `docs/plans/VOICE_IO.md` "Phase 6" names the first 3):

| Model | Why | Streaming | Runtime |
|---|---|---|---|
| NVIDIA Parakeet TDT 0.6B v3 | Very fast, 25 EU languages, word timestamps | Chunked (near real time) | NeMo; `parakeet-mlx` on Apple Silicon |
| Qwen3-ASR 0.6B | 50+ languages, high quality | Batch | transformers |
| Kyutai STT (1B / 2.6B) | Built for streaming, low latency | True streaming | `moshi` / `moshi-mlx` |
| faster-whisper (CTranslate2) | Same Whisper weights, 2-4x faster on CPU/CUDA, has VAD and word timestamps | Chunked | `faster-whisper` |
| NVIDIA Canary 1B Flash | High accuracy, translation | Batch | NeMo |
| Moonshine (tiny/base) | Very small, fast on CPU, English | Chunked | `useful-moonshine` / ONNX |

Recommendation: first replace the PyTorch Whisper path with **faster-whisper** (same models, faster, fixes the 30 s cut). Then add **Parakeet v3** for low latency on both platforms. Add **Kyutai STT** only if true streaming is required.

## 4. Streaming support

Voicebox STT does not support streaming now. Whisper is a 30 s window encoder-decoder model. It cannot output text while audio arrives. There are 3 ways to get "streaming" behavior:

1. **VAD + batch (pseudo-streaming).** Detect the end of speech with VAD, then transcribe each utterance in 1 call. This gives only final transcripts. Latency is the utterance transcription time (about 100-400 ms for `turbo` on a good GPU or Apple Silicon for a short phrase).
2. **Rolling window re-decode.** Transcribe the growing buffer every 300-500 ms and send interim text. When VAD detects the end, send the final text. This works with Whisper (LocalAgreement, as in `whisper_streaming`), but it uses much more GPU.
3. **Native streaming model.** Kyutai STT or Parakeet in cache-aware or chunked mode emit tokens as audio arrives.

The detailed streaming design (generic interface, per-model adapters, Qwen3-ASR incremental decode, Voxtral Realtime) is in `docs-internal/research/stt-streaming-design.md`.

The plan in `docs/plans/VOICE_IO.md:239` already describes a `/transcribe/stream` WebSocket: "Audio frames flow in, partial transcripts stream back". The `/tts/ws` route (`backend/routes/tts_stream.py:297`) is a good template for the protocol and for client disconnect and lock handling.

## 5. LiveKit compatibility

### 5.1 What LiveKit expects

LiveKit Agents (Python, 1.x) defines `livekit.agents.stt.STT`:

- `capabilities = STTCapabilities(streaming=..., interim_results=...)`.
- `_recognize_impl(buffer, *, language, conn_options) -> SpeechEvent` for batch recognition.
- `stream(...) -> RecognizeStream` for streaming. The stream reads `rtc.AudioFrame` objects from its input channel and sends `SpeechEvent`s: `START_OF_SPEECH`, `INTERIM_TRANSCRIPT`, `FINAL_TRANSCRIPT`, `END_OF_SPEECH`.
- If an STT has `streaming=False`, `AgentSession` wraps it in `stt.StreamAdapter` with the session VAD (Silero). The adapter sends each VAD utterance to `_recognize_impl` and emits 1 final transcript per utterance.

LiveKit audio frames are PCM16 mono, usually 48 kHz (often 16 kHz or 24 kHz after resample). Whisper needs 16 kHz mono float.

### 5.2 Options

| Option | Work on Voicebox | Work on the agent | Result |
|---|---|---|---|
| A. OpenAI-compatible endpoint | Add `POST /v1/audio/transcriptions` (planned in `docs/plans/OPENAI_SUPPORT.md`). Allow it in the API key gate. | Use the stock plugin: `openai.STT(base_url=".../v1", model="turbo", api_key=KEY)` + Silero VAD. No custom code. | Final transcripts only. Works today with LiveKit `StreamAdapter`. |
| B. Custom non-streaming plugin | Allow `/transcribe` (or a new `/stt/transcribe`) in the API key gate. Optional: accept raw PCM16 so the client does not have to build a WAV. | Small `VoiceboxSTT(stt.STT)` with `streaming=False`. `_recognize_impl` posts the buffer as WAV. | Same as A, but with a Voicebox-specific API. |
| C. Streaming WebSocket + streaming plugin | Build `WS /stt/ws`: PCM16 frames in, JSON `interim` / `final` / `speech_start` / `speech_end` out. Server-side VAD (Silero) + rolling Whisper decode, or a native streaming model. | `VoiceboxSTT` with `streaming=True, interim_results=True` and a `RecognizeStream` that maps the JSON events to `SpeechEvent`s. | Interim transcripts, better turn detection and barge-in. Most work. |

### 5.3 Recommendation

1. **Now: option A.** It is the least code and follows an existing plan. The OpenAI STT plugin is maintained by LiveKit. The `StreamAdapter` + Silero VAD gives good voice-agent behavior with Whisper `turbo`. Required Voicebox changes:
   - Add `POST /v1/audio/transcriptions` (fields: `file`, `model`, `language`, `response_format`, `prompt`). Map `whisper-1` to the configured default size.
   - Put the route under an allowed prefix, or add `/v1/audio/` to `EXTERNAL_PATH_PREFIXES` in `backend/utils/api_keys.py`.
   - Fix the 30 s cut on the PyTorch path.
   - Hold `inference_lock` (or a separate STT lock with priority) so that STT and TTS do not fight for the GPU, or measure that they do not need it.
   - Keep the selected Whisper model loaded (warmup endpoint like `/tts/warmup`) so the first turn does not load a model.
2. **Later: option C**, when interim transcripts or lower turn latency become necessary. Build it with the `/tts/ws` patterns. Use the same JSON event names that LiveKit uses, so the plugin is a thin mapper. Pair it with a streaming-capable engine (Parakeet or Kyutai) to keep GPU use low.

### 5.4 Sketch: option B / A plugin (agent side)

```python
from livekit import rtc
from livekit.agents import stt, utils, APIConnectOptions
import aiohttp

class VoiceboxSTT(stt.STT):
    def __init__(self, base_url: str, api_key: str, model: str = "turbo", language: str | None = None):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._url, self._key, self._model, self._lang = base_url, api_key, model, language

    async def _recognize_impl(self, buffer: utils.AudioBuffer, *, language=None, conn_options: APIConnectOptions) -> stt.SpeechEvent:
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self._model)
        if language or self._lang:
            form.add_field("language", language or self._lang)
        async with aiohttp.ClientSession() as s, s.post(
            f"{self._url}/transcribe", data=form, headers={"Authorization": f"Bearer {self._key}"},
            timeout=aiohttp.ClientTimeout(total=conn_options.timeout),
        ) as r:
            r.raise_for_status()
            text = (await r.json())["text"]
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=language or self._lang or "", text=text)],
        )

# session = AgentSession(stt=VoiceboxSTT(...), vad=silero.VAD.load(), tts=..., llm=...)
```

Note: `/transcribe` returns HTTP 202 while a model downloads. The plugin must treat 202 as a retryable error, or the server must preload the model.

## 6. Open questions

- Which language set does the agent need? Parakeet v3 covers only 25 European languages.
- Does the agent need interim transcripts (for fast barge-in and turn detection), or are final transcripts enough?
- ~~Does the LiveKit worker run on the same machine or through the tunnel?~~ Answered 2026-10-28: the LiveKit agent runs on the same Mac. It uses loopback, so the API key gate needs no change.

## 7. Direction: all voice processing local on the Mac

Decision (2026-10-28): all TTS and STT run locally on the Mac. No cloud speech service.

### 7.1 What Silero VAD is

- Silero VAD is a small open-source voice activity detection model (MIT license, about 2 MB). It is not a cloud service.
- VAD only decides "speech" or "no speech" for each 30 ms audio frame. It does not produce text.
- LiveKit ships it as the `livekit-plugins-silero` Python package. It runs on the CPU inside the LiveKit agent process, through ONNX Runtime. It uses less than 1 ms of CPU per frame.
- It downloads its model file once and then works offline.

### 7.2 What runs where

| Part | Runs on | Local? |
|---|---|---|
| TTS (Qwen, Kokoro, ...) | Voicebox server on the Mac, MLX | Yes, already |
| STT (Whisper) | Voicebox server on the Mac, MLX | Yes, already |
| VAD (Silero) | LiveKit agent process | Yes, if the agent runs on the Mac |
| Turn detector (optional LiveKit model) | LiveKit agent process | Yes, local ONNX model |
| LLM | Agent config | Not in scope of this card |
| LiveKit server (media routing) | LiveKit Cloud or self-hosted | Transports audio only; no speech processing |

The `openai.STT` plugin in option A is only an HTTP client. With `base_url` pointed at Voicebox, no audio goes to OpenAI.

### 7.3 Effect on the recommendations

- On the Mac, Voicebox always uses the MLX path (`MLXSTTBackend`). The PyTorch 30 s cut does not affect this setup. That fix gets low priority.
- Mac STT candidates (MLX, Apple Silicon): Whisper turbo (`openai/whisper-large-v3-turbo`, now), Qwen3-ASR (section 8), `parakeet-mlx` (Parakeet TDT 0.6B v3, fast, chunked streaming), `moshi-mlx` (Kyutai STT, true streaming). faster-whisper and NeMo do not use Apple Silicon GPUs and are not useful here.
- STT and TTS share 1 Apple Silicon GPU and 1 unified memory pool. Keep both models loaded (for example Whisper turbo about 1.6 GB plus Qwen TTS 0.6B about 1.2 GB). Serialize GPU work with `inference_lock` or measure first.
- If the LiveKit agent also runs on the Mac, it calls Voicebox on `127.0.0.1`. The API key gate does not apply to loopback, so no gate change is needed. Only if the agent runs on another machine does `/v1/audio/` (or `/stt/`) need to go into `EXTERNAL_PATH_PREFIXES`.
- Option C (streaming `WS /stt/ws`) can also run the Silero VAD inside Voicebox. Then the agent sends raw audio and gets speech events back. This is a later step.

## 8. Adding Qwen3-ASR to Voicebox

### 8.1 Findings

- The pinned `mlx-audio==0.4.1` already contains Qwen3-ASR: `mlx_audio/stt/models/qwen3_asr/` (checked in `backend/venv`). `mlx_audio.stt.load(repo)` selects it from `model_type = "qwen3_asr"`. No new dependency is needed on the Mac.
- Checkpoints (verified in phase 0, 2026-10-28): official `Qwen/Qwen3-ASR-0.6B` (1.88 GB) and `Qwen/Qwen3-ASR-1.7B` (4.70 GB); MLX 8-bit `mlx-community/Qwen3-ASR-0.6B-8bit` (1.01 GB) and `mlx-community/Qwen3-ASR-1.7B-8bit` (2.47 GB). All 4 load with `mlx_audio.stt.load`. Voicebox will register the 8-bit repos. Full list and measurements: `stt-implementation-plan.md`, phase 0 results.
- API: `model.generate(audio, language=..., stream=False)` returns an `STTOutput` with `.text`. It splits long audio itself at quiet points (`chunk_duration`, default 1200 s), so there is no 30 s limit.
- `generate(..., stream=True)` calls `stream_transcribe()`. This yields text tokens while it decodes. It still needs the complete audio first. It is output streaming, not live audio input.
- **Language parameter is different from Whisper.** Qwen3-ASR expects a language *name* that matches `config.support_languages` (for example "English"). Voicebox passes codes such as `en`. An unknown value goes into the prompt as is (`language en<asr_text>`), which can make the result worse. Map codes to names with `LANGUAGE_CODE_TO_NAME` (`backend/backends/__init__.py:26`) and capitalize them. With `language=None` the model detects the language, but this is not reliable: in phase 0 the 0.6B model detected German audio as English and gave wrong text. Always pass a language name.
- Supported languages: 30 (`config.support_languages`). Ukrainian is not in the list.
- Quality (phase 0, WER): 0.6B is as good as Whisper turbo on English, but clearly worse on German and Russian verse. 1.7B is close to Whisper turbo on German and Russian. Whisper turbo sometimes hallucinates on long audio; Qwen3-ASR did not.
- The same package also has `parakeet`, `moonshine`, `canary`, `sensevoice`, `voxtral` and `voxtral_realtime`. The same engine work makes those cheap to add later.

### 8.2 Steps

1. **Engine registry.** Add `STT_ENGINES = {"whisper": ..., "qwen_asr": ...}`. Change `get_stt_backend(engine)` to keep 1 instance per engine, like `_tts_backends` (`backend/backends/__init__.py:202`).
2. **Model configs.** Add `_get_qwen_asr_configs()` with `engine="qwen_asr"`, `model_name` `qwen-asr-0.6B` / `qwen-asr-1.7B`, `hf_repo_id` (the 8-bit `mlx-community` repos), `size_mb` and a long `languages` list. Add it to the STT config list, so download, status and unload in the model UI work. Update the load and unload switch (`backend/backends/__init__.py:557`, `:600`, `:630`), which today checks only `engine == "whisper"`.
3. **Backend class.** `MLXQwenASRBackend` in a new `backend/backends/qwen_asr_backend.py`. Copy `MLXSTTBackend` (`backend/backends/mlx_backend.py:371`): `load(repo)` in a thread with `model_load_progress`, and `transcribe()` that calls `self.model.generate(path, language=<mapped name>)` and returns `result.text.strip()`.
4. **Model selection in the API.** `/transcribe`, captures and MCP today accept only a Whisper size (`base|small|medium|large|turbo`). Accept a model name such as `whisper-turbo` or `qwen-asr-0.6B`, resolve it to engine + size through the configs, and keep the old size values for backward compatibility. Update `TranscriptionRequest` (`backend/models.py:171`), the `/transcribe` validation (`backend/routes/transcription.py:41`), `services/captures.py` and `mcp_server/tools.py:_transcribe_file`.
5. **Settings and frontend.** Capture settings store the STT model; allow the new names. Add the models to the STT model picker in `app/` and regenerate the API client.
6. **Memory.** Unload the other STT engine when a new one loads, or keep only 1 STT model in memory, as today.
6a. **Default.** Whisper turbo stays the default. `qwen-asr-1.7B` is the recommended Qwen3-ASR model for languages other than English, `qwen-asr-0.6B` for English (see `stt-implementation-plan.md`, phase 1, "Default model").
7. **Tests.** Unit test the language code mapping and the model name resolution. Run 1 manual check on the Mac: transcribe the same clip with `whisper-turbo` and `qwen-asr-0.6B` and compare text and time.

Estimated size: about 1-2 days for backend + API, plus the UI picker.

### 8.3 For LiveKit

Qwen3-ASR works with option A (`/v1/audio/transcriptions` + `openai.STT` + Silero VAD) the same way as Whisper: set `model="qwen-asr-0.6B"` for English or `model="qwen-asr-1.7B"` for other languages, and always pass `language`. For each VAD phrase it gives 1 final transcript. Later, `stream_transcribe` can send partial text over `WS /stt/ws` while it decodes 1 phrase.
