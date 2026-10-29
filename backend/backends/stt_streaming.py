"""
Streaming STT: 1 interface for all STT models, with an adapter per model.

Design: ``docs/research/stt-streaming-design.md`` sections 3-7.

Layers::

    client frames ──► StreamSession ──► VADSegmenter ──► model adapter ──► STTEvents
                      PCM16 → float32   Silero VAD        SegmentedBatchStream (any batch model)
                      resample 16 kHz   endpointing       RollingRedecodeStream (re-decode, interim text)
                                                          NativeStreamingStream (streaming model, append-only text)

``StreamSession`` implements ``STTStream``. It owns the event queue, the
segment buffer and 1 worker task that runs the model steps in order. Each
model step holds ``inference_lock`` only for that step, so TTS chunks can run
between STT steps. When the model is still busy at an interim tick, the tick
is skipped; audio is never dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

if TYPE_CHECKING:
    from . import ModelConfig, STTBackend

logger = logging.getLogger(__name__)

STREAM_SAMPLE_RATE = 16000


# ── Interface ────────────────────────────────────────────────────────────────


class STTEventType(StrEnum):
    SPEECH_START = "speech_start"  # VAD: speech started (new segment)
    INTERIM = "interim"  # text for the open segment; can still change
    FINAL = "final"  # text for a closed segment; never changes
    SPEECH_END = "speech_end"  # VAD: speech ended
    ERROR = "error"


@dataclass
class STTEvent:
    type: STTEventType
    segment_id: int
    text: str = ""  # full text of the segment so far (clients replace, never append)
    stable_text: str = ""  # prefix of `text` that is stable within the current hypothesis
    language: str | None = None  # ISO code, detected or forced
    start_s: float | None = None  # segment start, seconds from stream start
    end_s: float | None = None
    message: str | None = None  # for ERROR

    def to_message(self) -> dict[str, Any]:
        """The ``WS /stt/ws`` JSON message for this event."""
        fields = {
            STTEventType.SPEECH_START: ("segment_id", "start_s"),
            STTEventType.INTERIM: ("segment_id", "text", "stable_text"),
            STTEventType.FINAL: ("segment_id", "text", "language", "start_s", "end_s"),
            STTEventType.SPEECH_END: ("segment_id", "end_s"),
            STTEventType.ERROR: ("segment_id", "message"),
        }[self.type]
        return {"type": self.type.value, **{name: getattr(self, name) for name in fields}}


@dataclass(frozen=True)
class STTStreamCapabilities:
    interim_results: bool  # emits INTERIM events
    revises_text: bool  # INTERIM text can change (False = append-only)
    native_streaming: bool  # model is built for streaming (no re-decode)
    min_latency_ms: int  # typical time from speech to first text
    needs_vad: bool  # adapter needs external endpointing to emit FINAL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class STTStreamOptions:
    model: str  # registry model name (or old Whisper size), e.g. "qwen-asr-0.6B"
    language: str | None = None  # ISO code; None = model default
    sample_rate: int = STREAM_SAMPLE_RATE  # rate of the pushed audio; the session resamples
    interim_interval_ms: int = 500  # how often to refresh INTERIM (re-decode adapters)
    max_segment_s: float = 30.0  # force FINAL after this length
    vad: bool = True  # server VAD closes segments; False = the client calls end_segment()
    interim: bool = True  # emit INTERIM when the adapter can
    delay_ms: int | None = None  # native streaming models: transcription delay; None = adapter default


class STTStream(Protocol):
    """1 live recognition session. Not thread-safe; 1 producer."""

    async def push_audio(self, pcm: np.ndarray) -> None:
        """Mono float32 in [-1, 1] at options.sample_rate. Any frame size."""
        ...

    async def end_segment(self) -> None:
        """Endpoint: close the open segment now and emit its FINAL."""
        ...

    async def cancel_segment(self) -> None:
        """Drop the open segment with no FINAL (barge-in, false start)."""
        ...

    async def aclose(self) -> None:
        """end_segment(), then free the state. The events iterator ends."""
        ...

    def events(self) -> AsyncIterator[STTEvent]: ...


class StreamingSTTBackend(Protocol):
    def stream_capabilities(self, model: str) -> STTStreamCapabilities: ...

    def open_stream(self, options: STTStreamOptions) -> STTStream: ...


# ── Model adapters ───────────────────────────────────────────────────────────


class SegmentAdapter:
    """Model part of a stream. ``StreamSession`` calls 1 hook at a time.

    Hooks receive the complete 16 kHz audio of the open segment. Each call is
    1 model step and runs while the session holds ``inference_lock``.
    """

    capabilities: STTStreamCapabilities
    # Fixed interim tick for adapters that step on the new audio only (native
    # streaming). None = ``STTStreamOptions.interim_interval_ms``.
    interim_interval_ms: int | None = None

    async def prepare(self) -> None:
        """Load the model before the first step."""

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        """Return ``(text, stable_text)`` for the open segment, or None."""
        return None

    async def catch_up(self, audio: np.ndarray, segment_id: int) -> bool:
        """Before ``finalize()``: 1 short step on audio the model has not processed yet.

        Returns True while work remains. The session calls it in a loop, with
        the lock held for 1 call only, so a long backlog does not block TTS.
        """
        return False

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        """Return the final text of a closed segment."""
        raise NotImplementedError

    def reset(self, segment_id: int) -> None:
        """Drop per-segment state (after FINAL or cancel)."""


SEGMENTED_CAPABILITIES = STTStreamCapabilities(
    interim_results=False,
    revises_text=False,
    native_streaming=False,
    min_latency_ms=300,
    needs_vad=True,
)

_executors: dict[str, ThreadPoolExecutor] = {}
_executors_lock = threading.Lock()


def get_model_executor(model_name: str) -> ThreadPoolExecutor:
    """1 worker thread per model for blocking stream work.

    On MLX all models share the 1 MLX worker thread: MLX GPU streams belong
    to the thread that made them (see ``mlx_backend._mlx_executor``).
    """
    from ..utils.platform_detect import get_backend_type

    if get_backend_type() == "mlx":
        from .mlx_backend import _mlx_executor

        return _mlx_executor
    with _executors_lock:
        executor = _executors.get(model_name)
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"stt-{model_name}")
            _executors[model_name] = executor
        return executor


async def run_blocking(model_name: str, fn: Callable[..., Any], *args: Any) -> Any:
    """Run ``fn(*args)`` on the model's worker thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_model_executor(model_name), fn, *args)


def _write_wav(audio: np.ndarray) -> str:
    import soundfile as sf

    fd, path = tempfile.mkstemp(suffix=".wav", prefix="stt-stream-")
    os.close(fd)
    sf.write(path, audio, STREAM_SAMPLE_RATE, subtype="PCM_16")
    return path


class SegmentedBatchStream(SegmentAdapter):
    """Generic adapter for every batch ``STTBackend``: FINAL text only.

    It runs the batch path (``transcribe()`` on a temp WAV of the segment), so
    the FINAL text equals ``POST /transcribe`` on the same audio. With
    ``interim=True`` it also re-runs ``transcribe()`` on the whole segment at
    each tick. That cost grows with the segment length (Whisper encodes a
    padded 30 s window every time), so it is off by default.
    """

    def __init__(
        self,
        backend: STTBackend,
        config: ModelConfig,
        language: str | None = None,
        interim: bool = False,
    ):
        self.backend = backend
        self.config = config
        self.language = language
        self._interim = interim
        self.capabilities = replace(SEGMENTED_CAPABILITIES, interim_results=interim, revises_text=interim)

    async def prepare(self) -> None:
        from . import unload_other_stt_engines

        unload_other_stt_engines(self.config.engine)
        await self.backend.load_model(self.config.model_size)

    async def _transcribe(self, audio: np.ndarray) -> str:
        from . import unload_other_stt_engines

        path = await run_blocking(self.config.model_name, _write_wav, audio)
        try:
            # Same steps as services.transcribe.transcribe_with_model (batch path).
            unload_other_stt_engines(self.config.engine)
            return await self.backend.transcribe(path, self.language, self.config.model_size)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        if not self._interim:
            return None
        return await self._transcribe(audio), ""

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        return await self._transcribe(audio)


ROLLING_CAPABILITIES = STTStreamCapabilities(
    interim_results=True,
    revises_text=True,
    native_streaming=False,
    min_latency_ms=600,
    needs_vad=True,
)


def _common_prefix_len(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


class RollingRedecodeStream(SegmentAdapter):
    """Base class for encoder-decoder / audio-LLM models: re-decode at each tick.

    Design doc section 5.2. Each interim step:

    1. ``encode_incremental()`` with the open segment audio.
    2. Text prefix = the committed text tokens minus the last ``rollback_tokens``.
    3. ``decode(prefix, max_new_tokens)`` → hypothesis.
    4. LocalAgreement-2: the complete words that 2 hypotheses in a row agree
       on are committed. ``stable_text`` (the committed words) never gets
       shorter inside a segment. It is stable within the current hypothesis
       only: FINAL can still change it.

    The model hooks are blocking. They run on the model's worker thread, 1 at
    a time, so a subclass needs no locks for its segment state.
    """

    rollback_tokens = 5
    max_new_tokens = 24

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.capabilities = ROLLING_CAPABILITIES
        self.step_times_ms: list[float] = []  # interim step times of the current segment
        self._segment_id: int | None = None
        self._committed: list[str] = []  # committed words
        self._previous: list[str] = []  # words of the previous hypothesis

    # -- model hooks (blocking, on the worker thread) --

    def begin_segment(self) -> None:
        """Set up the per-segment model state."""

    def encode_incremental(self, audio: np.ndarray) -> None:
        """Update the encoder state. ``audio`` is the complete open segment so far."""
        raise NotImplementedError

    def decode(self, prefix_ids: list[int], max_new: int) -> list[int]:
        """Greedy decode on the current audio state, forced to start with ``prefix_ids``.

        Returns only the new tokens.
        """
        raise NotImplementedError

    def encode_text(self, text: str) -> list[int]:
        raise NotImplementedError

    def decode_text(self, ids: list[int]) -> str:
        raise NotImplementedError

    def free_segment(self) -> None:
        """Free the per-segment model state."""

    # -- SegmentAdapter --

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        return await run_blocking(self.model_name, self._interim_step, audio, segment_id)

    def reset(self, segment_id: int) -> None:
        # A step of this segment can still run on the worker thread (cancel
        # during a step), so the state is cleared there, after that step.
        get_model_executor(self.model_name).submit(self._clear, segment_id)

    # -- steps --

    def _begin(self, segment_id: int) -> None:
        self._clear(self._segment_id)
        self._segment_id = segment_id
        self.begin_segment()

    def _clear(self, segment_id: int | None) -> None:
        if segment_id is None or segment_id != self._segment_id:
            return
        if self.step_times_ms:
            times = sorted(self.step_times_ms)
            logger.info(
                "STT rolling %s segment %d: %d interim steps, p50 %.0f ms, max %.0f ms",
                self.model_name,
                segment_id,
                len(times),
                times[len(times) // 2],
                times[-1],
            )
        self.free_segment()
        self._segment_id = None
        self._committed = []
        self._previous = []
        self.step_times_ms = []

    def _interim_step(self, audio: np.ndarray, segment_id: int) -> tuple[str, str]:
        import time

        if segment_id != self._segment_id:
            self._begin(segment_id)
        started = time.perf_counter()
        self.encode_incremental(audio)
        committed_ids = self.encode_text(" ".join(self._committed)) if self._committed else []
        prefix = committed_ids[: max(0, len(committed_ids) - self.rollback_tokens)]
        new_ids = self.decode(prefix, self.max_new_tokens)
        hypothesis = self.decode_text(prefix + new_ids)
        text, stable = self.agree(hypothesis)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.step_times_ms.append(elapsed_ms)
        logger.debug(
            "STT rolling step %s seg %d: %.1f s audio, %.0f ms, stable=%r text=%r",
            self.model_name,
            segment_id,
            len(audio) / STREAM_SAMPLE_RATE,
            elapsed_ms,
            stable,
            text,
        )
        return text, stable

    def agree(self, hypothesis: str) -> tuple[str, str]:
        """LocalAgreement-2 on words. Returns ``(text, stable_text)``.

        The committed words stay at the start of ``text``, also when the
        rolled-back tokens decode differently this time. The last word of a
        hypothesis is never committed: the audio or the token limit can cut it.
        """
        words = hypothesis.split()
        n = len(self._committed)
        if words[:n] != self._committed:
            words = self._committed + words[n:]
        agreed = _common_prefix_len(words[:-1], self._previous[:-1])
        if agreed > n:
            self._committed = words[:agreed]
        self._previous = words
        return " ".join(words), " ".join(self._committed)


NATIVE_CAPABILITIES = STTStreamCapabilities(
    interim_results=True,
    revises_text=False,
    native_streaming=True,
    min_latency_ms=500,
    needs_vad=False,
)


class NativeStreamingStream(SegmentAdapter):
    """Base class for models built for streaming (causal encoder, append-only text).

    Design doc section 5.4. Each interim step gives ``feed()`` only the audio
    that arrived since the last step; the model keeps its encoder and decoder
    state between steps. ``finalize()`` feeds the rest of the segment, then
    ``flush()`` pushes silence to get the last words. The text never changes,
    so ``stable_text`` equals ``text``.

    The session gives the adapter the audio of open segments only, so no model
    step runs while the VAD reports silence between segments.

    The model hooks are blocking. They run on the model's worker thread, 1 at
    a time, so a subclass needs no locks for its segment state.
    """

    interim_interval_ms = 80  # 1 audio token of Voxtral Realtime

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.capabilities = NATIVE_CAPABILITIES
        self.step_times_ms: list[float] = []  # interim step times of the current segment
        self._segment_id: int | None = None
        self._consumed = 0  # segment samples given to feed()
        self._text = ""  # text of the last INTERIM

    # -- model hooks (blocking, on the worker thread) --

    def begin_segment(self) -> None:
        """Set up the per-segment model state."""

    def feed(self, audio: np.ndarray) -> str:
        """Process the new 16 kHz audio of the segment. Returns the segment text so far."""
        raise NotImplementedError

    def flush(self) -> str:
        """End of the segment: process the held-back audio and return the final text."""
        raise NotImplementedError

    def has_backlog(self) -> bool:
        """Whether fed audio waits for model steps (``feed()`` can limit its work)."""
        return False

    def free_segment(self) -> None:
        """Free the per-segment model state."""

    # -- SegmentAdapter --

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        return await run_blocking(self.model_name, self._interim_step, audio, segment_id)

    async def catch_up(self, audio: np.ndarray, segment_id: int) -> bool:
        return await run_blocking(self.model_name, self._catch_up_step, audio, segment_id)

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        return await run_blocking(self.model_name, self._final_step, audio, segment_id)

    def reset(self, segment_id: int) -> None:
        # A step of this segment can still run on the worker thread (cancel
        # during a step), so the state is cleared there, after that step.
        get_model_executor(self.model_name).submit(self._clear, segment_id)

    # -- steps --

    def _catch_up_step(self, audio: np.ndarray, segment_id: int) -> bool:
        self._feed_new(audio, segment_id)
        return self.has_backlog()

    def _feed_new(self, audio: np.ndarray, segment_id: int) -> str:
        if segment_id != self._segment_id:
            self._clear(self._segment_id)
            self._segment_id = segment_id
            self.begin_segment()
        new = audio[self._consumed :]
        self._consumed = len(audio)
        return self.feed(new)

    def _interim_step(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        import time

        started = time.perf_counter()
        text = self._feed_new(audio, segment_id)
        self.step_times_ms.append((time.perf_counter() - started) * 1000)
        if text == self._text:
            return None
        self._text = text
        return text, text

    def _final_step(self, audio: np.ndarray, segment_id: int) -> str:
        import time

        self._feed_new(audio, segment_id)
        started = time.perf_counter()
        text = self.flush()
        logger.info(
            "STT native %s segment %d: flush %.0f ms",
            self.model_name,
            segment_id,
            (time.perf_counter() - started) * 1000,
        )
        return text

    def _clear(self, segment_id: int | None) -> None:
        if segment_id is None or segment_id != self._segment_id:
            return
        if self.step_times_ms:
            times = sorted(self.step_times_ms)
            logger.info(
                "STT native %s segment %d: %d interim steps, p50 %.0f ms, max %.0f ms",
                self.model_name,
                segment_id,
                len(times),
                times[len(times) // 2],
                times[-1],
            )
        self.free_segment()
        self._segment_id = None
        self._consumed = 0
        self._text = ""
        self.step_times_ms = []


# ── Resampling ───────────────────────────────────────────────────────────────


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """PCM16 little-endian mono bytes → float32 in [-1, 1]."""
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


class Resampler:
    """Streaming resampler to 16 kHz (soxr, the librosa resampling backend).

    A stream resampler keeps its filter state between frames, so frame
    boundaries give no clicks. ``flush()`` returns the samples it holds back.
    """

    def __init__(self, in_rate: int, out_rate: int = STREAM_SAMPLE_RATE):
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._stream = None
        if in_rate != out_rate:
            import soxr

            self._stream = soxr.ResampleStream(in_rate, out_rate, 1, dtype="float32")

    def process(self, audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if self._stream is None:
            return audio
        return self._stream.resample_chunk(audio)

    def flush(self) -> np.ndarray:
        """Return the held-back samples and start a new stream state."""
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        tail = self._stream.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
        self._stream.clear()
        return tail


# ── VAD segmenter ────────────────────────────────────────────────────────────

VAD_FRAME_SAMPLES = 512  # 32 ms at 16 kHz (Silero VAD window)
VAD_FRAME_MS = VAD_FRAME_SAMPLES * 1000 // STREAM_SAMPLE_RATE


@dataclass
class VADSettings:
    threshold: float = 0.5  # speech probability that counts as speech
    neg_threshold: float = 0.35  # below this counts as silence (Silero default: threshold - 0.15)
    start_ms: int = 200  # speech this long opens a segment
    end_ms: int = 500  # silence this long closes it
    preroll_ms: int = 300  # audio kept before the speech start


_silero_session = None
_silero_lock = threading.Lock()


def _silero_model_path() -> str:
    """The ONNX file from the silero-vad package, found without importing it (it imports torch)."""
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.find_spec("silero_vad")
    if spec is not None and spec.submodule_search_locations:
        base = Path(next(iter(spec.submodule_search_locations)))
    else:
        base = Path(getattr(sys, "_MEIPASS", "")) / "silero_vad"  # frozen build: data file only
    return str(base / "data" / "silero_vad.onnx")


class SileroVAD:
    """Silero VAD v5 (ONNX, CPU): speech probability for each 512-sample frame.

    The ONNX session is shared; the recurrent state is per stream.
    """

    _CONTEXT = 64  # samples of the previous frame that the model sees at 16 kHz

    def __init__(self):
        global _silero_session
        with _silero_lock:
            if _silero_session is None:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                _silero_session = ort.InferenceSession(
                    _silero_model_path(), sess_options=opts, providers=["CPUExecutionProvider"]
                )
        self._session = _silero_session
        self._sr = np.array(STREAM_SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self._CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, frame.astype(np.float32)])[None, :]
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[0, -self._CONTEXT :]
        return float(out[0, 0])


@dataclass
class SegmenterEvent:
    kind: str  # "start" | "end"
    sample: int  # stream position (16 kHz samples) of the segment start / end


class VADSegmenter:
    """Endpointing on 32 ms frames.

    ``process()`` returns the audio that belongs to the open segment and the
    start / end events, in order. A segment starts after ``start_ms`` of speech
    and includes ``preroll_ms`` of audio before the speech. It ends after
    ``end_ms`` of silence.
    """

    def __init__(self, prob_fn: Callable[[np.ndarray], float], settings: VADSettings | None = None):
        self.prob_fn = prob_fn
        self.settings = settings or VADSettings()
        self.in_speech = False
        self._pending = np.zeros(0, dtype=np.float32)
        self._position = 0  # samples processed into frames
        self._speech_ms = 0
        self._silence_ms = 0
        keep_frames = (self.settings.preroll_ms + self.settings.start_ms) // VAD_FRAME_MS + 2
        self._ring: deque[np.ndarray] = deque(maxlen=keep_frames)

    def process(self, audio: np.ndarray) -> list[SegmenterEvent | np.ndarray]:
        """Feed 16 kHz audio. Returns segment audio arrays and events, in order."""
        out: list[SegmenterEvent | np.ndarray] = []
        buf = np.concatenate([self._pending, audio]) if len(self._pending) else audio
        n_frames = len(buf) // VAD_FRAME_SAMPLES
        for i in range(n_frames):
            frame = buf[i * VAD_FRAME_SAMPLES : (i + 1) * VAD_FRAME_SAMPLES]
            self._process_frame(frame, out)
            self._position += VAD_FRAME_SAMPLES
        self._pending = buf[n_frames * VAD_FRAME_SAMPLES :].copy()
        return out

    def force_end(self) -> None:
        """The segment was closed from outside (client endpoint or max length)."""
        self.in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._ring.clear()

    def _process_frame(self, frame: np.ndarray, out: list) -> None:
        s = self.settings
        prob = self.prob_fn(frame)
        if not self.in_speech:
            self._ring.append(frame)
            self._speech_ms = self._speech_ms + VAD_FRAME_MS if prob >= s.threshold else 0
            if self._speech_ms >= s.start_ms:
                frames_back = (self._speech_ms + s.preroll_ms) // VAD_FRAME_MS
                frames = list(self._ring)[-frames_back:]
                start = self._position + VAD_FRAME_SAMPLES - len(frames) * VAD_FRAME_SAMPLES
                self.in_speech = True
                self._silence_ms = 0
                self._ring.clear()
                out.append(SegmenterEvent("start", start))
                out.append(np.concatenate(frames))
            return

        out.append(frame)
        self._silence_ms = self._silence_ms + VAD_FRAME_MS if prob < s.neg_threshold else 0
        if self._silence_ms >= s.end_ms:
            self.in_speech = False
            self._speech_ms = 0
            out.append(SegmenterEvent("end", self._position + VAD_FRAME_SAMPLES))


# ── Session ──────────────────────────────────────────────────────────────────


@dataclass
class _Job:
    kind: str  # "interim" | "final"
    segment_id: int
    audio: np.ndarray
    start_s: float = 0.0
    end_s: float = 0.0


_CLOSED = object()


class StreamSession:
    """``STTStream`` implementation shared by all adapters.

    Call ``await start()`` before the first ``push_audio()``.
    """

    def __init__(
        self,
        adapter: SegmentAdapter,
        options: STTStreamOptions,
        vad: VADSegmenter | None = None,
        lock: asyncio.Lock | None = None,
    ):
        if lock is None:
            from ..services.task_queue import inference_lock

            lock = inference_lock
        self.adapter = adapter
        self.options = options
        self.lock = lock
        self.vad = vad
        if options.vad and vad is None:
            self.vad = VADSegmenter(SileroVAD())
        if not options.vad:
            self.vad = None

        self.capabilities = adapter.capabilities
        self._interim_enabled = options.interim and adapter.capabilities.interim_results
        self._resampler = Resampler(options.sample_rate)
        self._events: asyncio.Queue = asyncio.Queue()
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None
        self._busy = False
        self._closed = False

        self._position = 0  # 16 kHz samples received
        self._next_id = 0
        self._open_id: int | None = None
        self._open_start = 0
        self._chunks: list[np.ndarray] = []
        self._chunk_samples = 0
        self._interim_samples = 0  # segment length at the last interim job

        # Counters for logs and tests.
        self.ticks = 0
        self.skipped_ticks = 0

    # -- lifecycle --

    async def start(self) -> None:
        async with self.lock:
            await self.adapter.prepare()
        self._worker = asyncio.create_task(self._run_jobs())
        if self._interim_enabled:
            self._ticker = asyncio.create_task(self._tick())

    async def aclose(self, finalize: bool = True) -> None:
        """Close the stream. ``finalize=False`` drops the open segment and queued work."""
        if self._closed:
            return
        if finalize:
            await self.end_segment()
        else:
            self._drop_open_segment()
            while not self._jobs.empty():
                self._jobs.get_nowait()
        self._closed = True
        if self._ticker is not None:
            self._ticker.cancel()
        if self._worker is not None:
            if finalize:
                self._jobs.put_nowait(None)
                await self._worker
            else:
                self._worker.cancel()
                with contextlib.suppress(BaseException):
                    await self._worker
        self._events.put_nowait(_CLOSED)

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            event = await self._events.get()
            if event is _CLOSED:
                return
            yield event

    # -- audio in --

    async def push_pcm16(self, data: bytes) -> None:
        await self.push_audio(pcm16_to_float32(data))

    async def push_audio(self, pcm: np.ndarray) -> None:
        if self._closed:
            raise RuntimeError("stream is closed")
        self._ingest(self._resampler.process(pcm))

    def _ingest(self, audio: np.ndarray) -> None:
        if not len(audio):
            return
        if self.vad is None:
            if self._open_id is None:
                self._open_segment(self._position, emit=False)
            self._append(audio)
            self._position += len(audio)
            self._check_max_length()
            return

        for item in self.vad.process(audio):
            if isinstance(item, SegmenterEvent):
                if item.kind == "start":
                    self._open_segment(item.sample, emit=True)
                else:
                    self._close_segment(item.sample, speech_end=True)
            elif self._open_id is not None:
                self._append(item)
                self._check_max_length()
        self._position += len(audio)

    # -- segment control --

    async def end_segment(self) -> None:
        self._ingest(self._resampler.flush())
        if self.vad is not None:
            if self._open_id is not None:
                self.vad.force_end()
            self._close_segment(self._position, speech_end=True)
        else:
            self._close_segment(self._position, speech_end=False)

    async def cancel_segment(self) -> None:
        self._drop_open_segment()
        if self.vad is not None:
            self.vad.force_end()

    def _open_segment(self, start_sample: int, emit: bool) -> None:
        self._next_id += 1
        self._open_id = self._next_id
        self._open_start = start_sample
        self._chunks = []
        self._chunk_samples = 0
        self._interim_samples = 0
        if emit:
            self._emit(STTEvent(STTEventType.SPEECH_START, self._open_id, start_s=self._seconds(start_sample)))

    def _append(self, audio: np.ndarray) -> None:
        self._chunks.append(audio)
        self._chunk_samples += len(audio)

    def _segment_audio(self) -> np.ndarray:
        if len(self._chunks) > 1:
            self._chunks = [np.concatenate(self._chunks)]
        return self._chunks[0] if self._chunks else np.zeros(0, dtype=np.float32)

    def _close_segment(self, end_sample: int, speech_end: bool) -> None:
        seg_id = self._open_id
        if seg_id is None:
            return
        audio = self._segment_audio()
        start_s = self._seconds(self._open_start)
        end_s = self._seconds(max(end_sample, self._open_start + len(audio)))
        self._open_id = None
        self._chunks = []
        self._chunk_samples = 0
        if speech_end:
            self._emit(STTEvent(STTEventType.SPEECH_END, seg_id, end_s=end_s))
        if len(audio):
            self._jobs.put_nowait(_Job("final", seg_id, audio, start_s, end_s))
        else:
            self.adapter.reset(seg_id)

    def _drop_open_segment(self) -> None:
        if self._open_id is not None:
            self.adapter.reset(self._open_id)
        self._open_id = None
        self._chunks = []
        self._chunk_samples = 0

    def _check_max_length(self) -> None:
        if self._open_id is None:
            return
        if self._chunk_samples < self.options.max_segment_s * STREAM_SAMPLE_RATE:
            return
        # Long monologue: close the segment so state and decode cost stay bounded.
        end = self._open_start + self._chunk_samples
        if self.vad is not None and self.vad.in_speech:
            self._close_segment(end, speech_end=True)
            self._open_segment(end, emit=True)
        else:
            self._close_segment(end, speech_end=self.vad is not None)

    # -- model work --

    async def _tick(self) -> None:
        interval_ms = self.adapter.interim_interval_ms or self.options.interim_interval_ms
        interval = interval_ms / 1000
        while True:
            await asyncio.sleep(interval)
            if self._open_id is None or self._chunk_samples <= self._interim_samples:
                continue
            self.ticks += 1
            if self._busy or not self._jobs.empty():
                self.skipped_ticks += 1
                continue
            self._interim_samples = self._chunk_samples
            self._jobs.put_nowait(_Job("interim", self._open_id, self._segment_audio()))

    async def _run_jobs(self) -> None:
        while True:
            job = await self._jobs.get()
            if job is None:
                return
            if job.kind == "interim" and job.segment_id != self._open_id:
                continue  # segment closed or cancelled while queued
            self._busy = True
            try:
                if job.kind == "final":
                    more = True
                    while more:
                        async with self.lock:
                            more = await self.adapter.catch_up(job.audio, job.segment_id)
                async with self.lock:
                    if job.kind == "final":
                        text = await self.adapter.finalize(job.audio, job.segment_id)
                    else:
                        result = await self.adapter.interim(job.audio, job.segment_id)
                if job.kind == "final":
                    self.adapter.reset(job.segment_id)
                    self._emit(
                        STTEvent(
                            STTEventType.FINAL,
                            job.segment_id,
                            text=text,
                            language=self.options.language,
                            start_s=job.start_s,
                            end_s=job.end_s,
                        )
                    )
                elif result is not None and job.segment_id == self._open_id:
                    text, stable = result
                    self._emit(STTEvent(STTEventType.INTERIM, job.segment_id, text=text, stable_text=stable))
            except Exception as e:
                logger.exception("STT stream %s step failed", job.kind)
                self._emit(STTEvent(STTEventType.ERROR, job.segment_id, message=str(e)))
            finally:
                self._busy = False

    # -- helpers --

    def _emit(self, event: STTEvent) -> None:
        self._events.put_nowait(event)

    @staticmethod
    def _seconds(samples: int) -> float:
        return round(samples / STREAM_SAMPLE_RATE, 3)
