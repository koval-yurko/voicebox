# Streaming STT: generic interface and per-model adapters

Date: 2026-10-28. Base commit: `3abe9e1`. Status: design proposal, no code yet.
Context: `docs/research/stt-investigation.md` (current STT state, sections 7 and 8).
Target: Apple Silicon Mac, MLX, LiveKit agent on the same Mac.

## 1. Problem

Today every STT path is batch: the complete audio goes in, 1 string comes out.

- Voicebox writes the upload to a file and calls `STTBackend.transcribe(path)`.
- In `mlx-audio`, Qwen3-ASR `generate(stream=True)` streams the *output* tokens, but it encodes the *complete* audio before the first token.
- Voxtral Realtime in `mlx-audio` is a streaming model, but its implementation also reads a complete file.

Goal: audio arrives in small frames (20-100 ms). Text comes back as soon as possible: interim text while the user speaks, final text when the phrase ends.

## 2. Design rules

1. **1 interface for all models.** The transport (WebSocket, LiveKit plugin) does not know which model runs.
2. **Each model declares what it can do** (capabilities). The interface does not hide the differences; it describes them.
3. **Every batch model works with no model work**, through a generic adapter. Better streaming is an optional upgrade per model.
4. **Audio is never dropped.** If the model is slower than real time, the adapter skips interim updates, not audio.
5. **Model calls are blocking MLX code.** They run in 1 worker thread per loaded model. They share the GPU lock with TTS.

## 3. The interface

New file: `backend/backends/stt_streaming.py`.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Optional, Protocol
import numpy as np


class STTEventType(str, Enum):
    SPEECH_START = "speech_start"   # VAD: speech started (new segment)
    INTERIM = "interim"             # text for the open segment; can still change
    FINAL = "final"                 # text for a closed segment; never changes
    SPEECH_END = "speech_end"       # VAD: speech ended
    ERROR = "error"


@dataclass
class STTEvent:
    type: STTEventType
    segment_id: int
    text: str = ""                  # full text of the segment so far
    stable_text: str = ""           # prefix of `text` that will not change any more
    language: Optional[str] = None  # ISO code, detected or forced
    start_s: Optional[float] = None # segment start, seconds from stream start
    end_s: Optional[float] = None
    message: Optional[str] = None   # for ERROR


@dataclass(frozen=True)
class STTStreamCapabilities:
    interim_results: bool           # emits INTERIM events
    revises_text: bool              # INTERIM text can change (False = append-only)
    native_streaming: bool          # model is built for streaming (no re-decode)
    min_latency_ms: int             # typical time from speech to first text
    needs_vad: bool                 # adapter needs external endpointing to emit FINAL


@dataclass
class STTStreamOptions:
    model: str                      # registry model name, e.g. "qwen-asr-0.6B"
    language: Optional[str] = None  # ISO code; None = detect
    sample_rate: int = 16000        # rate of the pushed audio; the session resamples
    interim_interval_ms: int = 500  # how often to refresh INTERIM (re-decode adapters)
    max_segment_s: float = 30.0     # force FINAL after this length


class STTStream(Protocol):
    """1 live recognition session. Not thread-safe; 1 producer."""

    async def push_audio(self, pcm: np.ndarray) -> None:
        """Mono float32 in [-1, 1] at options.sample_rate. Any frame size."""

    async def end_segment(self) -> None:
        """Endpoint: close the open segment now and emit its FINAL."""

    async def cancel_segment(self) -> None:
        """Drop the open segment with no FINAL (barge-in, false start)."""

    async def aclose(self) -> None:
        """end_segment(), then free the state. The events iterator ends."""

    def events(self) -> AsyncIterator[STTEvent]:
        ...


class StreamingSTTBackend(Protocol):
    def stream_capabilities(self, model: str) -> STTStreamCapabilities: ...
    def open_stream(self, options: STTStreamOptions) -> STTStream: ...
```

Notes:

- `text` is always the full segment text, not a delta. Clients replace; they never append. This makes revising models and append-only models look the same to the client.
- `stable_text` lets a UI or an agent act early on text that will not change.
- `SPEECH_START` / `SPEECH_END` come from the VAD layer (section 4.2), not from the model.
- The existing batch `STTBackend` protocol stays. The batch `/transcribe` route does not change.

## 4. Shared layers (model independent)

```
 client frames ──► [Transport] ──► [StreamSession] ──► [Segmenter / VAD] ──► [Model adapter] ──► events
                   WS /stt/ws       resample to 16 kHz   Silero VAD, endpoint   implements STTStream
                   LiveKit plugin   ring buffer          max_segment_s
```

### 4.1 StreamSession

- Converts PCM16 to float32 and resamples to 16 kHz (`librosa`, already in `backend/requirements.txt` and used by `backend/utils/audio.py`).
- Owns the asyncio event queue and the worker thread handoff (`asyncio.to_thread` on a 1-thread executor per model).
- Coalesces work: if a model step is still running when the next interim tick comes, it skips that tick. Audio still goes into the buffer.

### 4.2 Segmenter (VAD)

- Server-side Silero VAD (ONNX, CPU) on 32 ms frames. This is a new dependency: `silero-vad` (or `onnxruntime` + the model file). Neither is in the requirements today. Settings: start after 200 ms of speech, end after 500 ms of silence, 300 ms pre-roll.
- On speech start: emits `SPEECH_START` and opens a segment in the adapter.
- On speech end: emits `SPEECH_END` and calls `end_segment()`.
- It can be turned off (`vad: false`) when the client does endpointing itself, for example the LiveKit agent with its own VAD and turn detector. Then the client calls `end_segment()`.
- `max_segment_s` forces a FINAL in long monologues, so that the state and the re-decode cost stay bounded.

### 4.3 Locking

- Each adapter step (1 encode + 1 short decode) holds `inference_lock` only for that step, about 50-200 ms. TTS chunks can run between STT steps.
- A later option: a priority lock where TTS for the active agent turn goes first.

## 5. Model adapters

### 5.1 `SegmentedBatchStream` — generic, every batch model

- Wraps any existing `STTBackend` (Whisper, Qwen3-ASR batch, Parakeet, ...).
- It buffers the segment audio. On `end_segment()` it calls `transcribe()` once and emits FINAL.
- Capabilities: `interim_results=False`, `revises_text=False`, `native_streaming=False`, `needs_vad=True`.
- Optional "poor man's interim": re-run `transcribe()` on the whole segment buffer every `interim_interval_ms`. The cost grows with the segment length. For Whisper, every run encodes a padded 30 s window, so use it only for short phrases.
- This is the same behavior as the LiveKit `StreamAdapter`, but on the server.

### 5.2 `RollingRedecodeStream` — base class for encoder-decoder / audio-LLM models

A generic algorithm with model hooks:

```python
class RollingRedecodeStream(STTStream):
    # hooks a model implements
    def encode_incremental(self, new_audio: np.ndarray) -> None: ...   # update encoder state
    def decode(self, text_prefix_ids: list[int], max_new: int) -> list[int]: ...
    def finalize(self) -> list[int]: ...                                # best full decode of the segment
```

Every `interim_interval_ms`:

1. `encode_incremental(new audio)`.
2. Build the text prefix: the committed tokens minus the last `rollback_tokens` (default 5).
3. `decode(prefix, max_new=24)` → hypothesis.
4. Stability (LocalAgreement-2): the longest common prefix of this hypothesis and the previous one is `stable_text`. Do not reduce `stable_text` later.
5. Emit INTERIM with `text = hypothesis`, `stable_text`.

On `end_segment()`: `finalize()` → FINAL; reset the state.

### 5.3 Qwen3-ASR adapter (on top of 5.2)

Facts from `mlx_audio/stt/models/qwen3_asr/qwen3_asr.py` (mlx-audio 0.4.1):

- Mel at 100 frames/s. The conv stem works on independent 1 s blocks (`n_window=50` → 100 frames), and it gives 13 audio tokens for each 1 s.
- The encoder attention is block-local: windows of `n_window_infer=800` frames = 8 s (104 tokens). The positional embedding restarts in each 1 s block.
- **Result: the encoder output for a closed 8 s window never changes when more audio arrives.** Only the open window (at most 8 s) must be encoded again.
- The prompt is `system, <audio_start>, audio tokens, <audio_end>, assistant, "language X<asr_text>", text`. The text decoder is causal, with a KV cache (`make_cache()`), and `mlx_lm.generate_step` accepts `prompt_cache` and `input_embeddings`.

Incremental state:

| State | Changes when |
|---|---|
| `closed_audio_tokens` (encoder output of closed 8 s windows) | a window closes (every 8 s) |
| `prefix_cache` (decoder KV for `system + <audio_start> + closed_audio_tokens`) | a window closes; extend it by prefilling only the new 104 tokens |
| `open_window_audio` (≤ 8 s of samples) | every push |
| `committed_text_ids` | every interim step |

Each interim step:

1. Mel + encoder on the open window only (≤ 800 frames).
2. Copy the prefix cache position (trim back to the closed-window boundary with `cache.trim()`; `KVCache.is_trimmable()` is true).
3. Prefill: open-window audio embeds + `<audio_end><|im_end|>` + assistant header + language prefix + text prefix tokens.
4. Greedy decode, at most 24 new tokens or EOS.

Measured cost per interim step in phase 0 (Apple M4 Max, audio fed at real-time speed, English, German and Russian clips):

| Model | Step p50 / p95 / max | First interim text after speech start |
|---|---|---|
| 0.6B (official bf16) | 70 / 91 / 130 ms | about 0.55 s |
| 1.7B (official bf16) | 114 / 147 / 186 ms | about 0.6 s |
| 0.6B 8-bit | 51 / 74 / 124 ms | about 0.54 s |
| 1.7B 8-bit | 94 / 155 / 204 ms | about 0.57 s |

With a 500 ms interval, this is faster than real time. The interval can go down to 250 ms. Details: `stt-implementation-plan.md`, phase 0 results, 0.3.

Rules from the phase 0 prototype:

- **`finalize()` runs the batch path** (`model.generate()` on the segment audio). Use the prefix cache only for interim steps. If `finalize()` decodes on top of the prefix cache, only 88-95 % of segments give the same text as batch mode: in bf16 the top 2 tokens of hard words are almost equal, and any change in how the prompt is split into prefill calls can select the other token. The batch path costs the same time (final latency p50 about 100 ms for 0.6B, 190 ms for 1.7B 8-bit).
- **Prefill like `mlx_lm.generate_step`:** all prompt tokens except the last in 1 call, then the last token alone.
- **Mel at a window edge:** compute the mel for a window from 320 samples (2 hops) before the window start, and drop the first 2 frames. Then the frames are exactly equal to batch mode. Close a window only when at least 40 samples after its end have arrived: the last frame of the window is centred 160 samples before the window end and uses 200 samples on each side. (The phase 0 prototype used 360; phase 4 measured that 40 gives frames equal to batch mode.)
- **Mel normalization:** `WhisperFeatureExtractor` clamps to `max - 8.0` over the complete segment. Use the maximum of final frames only (closed windows + the current open window), not a running maximum, because the last open frame changes when audio arrives. Encode a closed window again if the segment maximum later grows enough to change its clamp.
- **Partial chunk:** pad the last partial 1 s chunk of a window to 100 frames, as batch mode does.
- **Language:** always pass the name, not the code (see `stt-investigation.md` 8.1). Do not use `language=None`; auto-detection was wrong for German in phase 0.
- **Interim stability:** LocalAgreement-2 commits the first words after about 1 s of audio, and the final text changed committed words in 20-36 % of segments (for example "Mr" → "Mister"). `stable_text` is stable within the current hypothesis only. FINAL replaces all interim text.

### 5.4 Voxtral Realtime adapter (`NativeStreamingStream`)

Facts from `mlx_audio/stt/models/voxtral_realtime/` (Voxtral Mini 4B Realtime 2602):

- The encoder is causal (causal conv stem + 32 causal layers, sliding window 750, `encode_chunks` already uses `RotatingKVCache`).
- The decoder emits 1 token for each 80 ms audio frame, with a configured delay (`transcription_delay_ms`, default 480 ms).
- The text is append-only. It never changes.

Incremental changes:

1. Keep the causal conv tail (the last `kernel_size - stride` input frames) between pushes.
2. Keep the encoder `RotatingKVCache`s between pushes. Encode only the new frames.
3. Keep the decoder cache. For each new 80 ms audio embed, run 1 decoder step (audio embed + previous token embed) and emit text when the token is not a pad token.
4. `end_segment()`: push about `delay` ms of silence to get the last words, then FINAL.

Capabilities: `interim_results=True`, `revises_text=False`, `native_streaming=True`, `min_latency_ms≈500`, `needs_vad=False` (VAD only marks the segments).

Cost: 1 step of a 4B decoder every 80 ms, also during silence (or pause it while the VAD reports silence). This keeps the GPU busy all the time and competes with TTS. It needs about 2.5 GB (4-bit) to 8 GB (bf16) of memory. Use it only if the Qwen3-ASR adapter is too slow or too unstable.

Implemented in phase 5 (`NativeStreamingStream` in `backend/backends/stt_streaming.py`, `VoxtralNativeStream` in `backend/backends/voxtral_streaming.py`; results in `stt-implementation-plan.md`). Changes to the plan above:

- Default delay 240 ms, not 480 ms. At 480 ms the model emits a word about 0.7 s after the word end, also in batch mode, so the text latency was 0.8 s. The client can set `delay_ms` per stream.
- `end_segment()` pushes (delay + 1) tokens of silence. The model emits no EOS, and more silence gives no more text.
- The state after the 32 left-pad silence tokens is the same for every segment. It is computed once and copied at each segment start.
- Each interim step runs at most 4 decoder steps. Before FINAL, the session calls `catch_up()` in a loop, 1 lock hold per call. So audio that waited during a TTS request does not block the next TTS request.
- The VAD gives the adapter the audio of open segments only, so no step runs in the silence between segments.

### 5.5 Whisper

- Default: `SegmentedBatchStream` (FINAL only). This is enough for LiveKit with VAD.
- Interim text with Whisper uses the same `RollingRedecodeStream` hooks, but each step encodes a padded 30 s window. It is much more expensive than Qwen3-ASR, so it is not recommended.

### 5.6 Later models

| Model (`mlx-audio` 0.4.1) | Adapter |
|---|---|
| `parakeet` (TDT) | `RollingRedecodeStream` with chunked encoding, or `SegmentedBatchStream` first |
| `moonshine` | `SegmentedBatchStream` (fast enough to re-run) |
| `canary`, `sensevoice`, `voxtral` (non-realtime) | `SegmentedBatchStream` |

## 6. Registry

Add the streaming info to `ModelConfig` (`backend/backends/__init__.py:49`):

```python
stream_adapter: str = "segmented"   # "segmented" | "rolling" | "native"
```

`get_stt_stream(options)` selects the backend by model name and asks it for `open_stream()`. If a backend has no special adapter, it gets `SegmentedBatchStream`.

## 7. Transport: `WS /stt/ws`

Same style as `WS /tts/ws` (`backend/routes/tts_stream.py:297`).

Client → server:

- `{"type": "start", "model": "qwen-asr-0.6B", "language": "en", "sample_rate": 16000, "vad": true, "interim": true}`
  - optional `"delay_ms"`: Voxtral Realtime transcription delay (multiple of 80 from 80 to 1200, or 2400; default 240). Other models ignore it.
- binary frames: PCM16 little-endian mono at `sample_rate`
- `{"type": "end_segment"}` — client endpoint (when `vad: false`)
- `{"type": "cancel_segment"}`
- `{"type": "end"}` — finish and close

Server → client:

- `{"type": "ready", "capabilities": {...}}`
- `{"type": "speech_start", "segment_id", "start_s"}`
- `{"type": "interim", "segment_id", "text", "stable_text"}`
- `{"type": "final", "segment_id", "text", "language", "start_s", "end_s"}`
- `{"type": "speech_end", "segment_id", "end_s"}`
- `{"type": "error", "message"}` / `{"type": "end"}`

## 8. LiveKit plugin

`VoiceboxSTT(stt.STT)` with `STTCapabilities(streaming=True, interim_results=caps.interim_results)`.

`RecognizeStream._run()`:

1. Open `ws://127.0.0.1:<port>/stt/ws` and send `start`.
2. Read `rtc.AudioFrame`s from the input channel. Resample them to 16 kHz with `rtc.AudioResampler`, then send them as binary frames.
3. On a `FlushSentinel` from the framework, send `end_segment`.
4. Map the events:

| Voicebox | LiveKit `SpeechEventType` |
|---|---|
| `speech_start` | `START_OF_SPEECH` |
| `interim` | `INTERIM_TRANSCRIPT` (text = `text`) |
| `final` | `FINAL_TRANSCRIPT` |
| `speech_end` | `END_OF_SPEECH` |

Endpointing choice: either the server VAD (`vad: true`, the plugin forwards the events), or the agent VAD and turn detector (`vad: false`, the agent sends `end_segment`). Start with the server VAD. The agent can still use its turn detector on the transcripts.

## 9. Plan

1. **Interface + `SegmentedBatchStream` + `WS /stt/ws` + server VAD.** All current models work at once with FINAL only. This step also tests the transport and the LiveKit plugin.
2. **Prototype the Qwen3-ASR rolling adapter as a script.** Feed a WAV at real-time speed. Log each INTERIM, FINAL and step time. Compare the FINAL text with batch `generate()`. Exit criteria: step time < interval, FINAL equal to batch for at least 95 % of segments on a test set.
3. **Move the adapter into Voicebox** behind `stream_adapter="rolling"` (needs the Qwen3-ASR engine from `stt-investigation.md` section 8).
4. **LiveKit plugin with interim results.**
5. **Only if needed: Voxtral Realtime native adapter.**

Risks:

- The `mlx-audio` internals (`audio_tower`, `_build_inputs_embeds`, cache layout) are not a public API. Pin `mlx-audio==0.4.1` and cover the adapter with a test that compares it with batch output, so an upgrade that breaks it fails fast.
- STT steps and TTS chunks share 1 GPU. Measure the TTS first-chunk latency while STT streams.
