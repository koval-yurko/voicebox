"""
Unit tests for the streaming STT layer (backend/backends/stt_streaming.py).
No model download needed: the STT model is a fake. The VAD test runs the real
Silero ONNX model on a speech fixture.
"""

import asyncio
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend import backends
from backend.backends.stt_streaming import (
    STREAM_SAMPLE_RATE,
    Resampler,
    SegmentedBatchStream,
    SegmenterEvent,
    SileroVAD,
    StreamSession,
    STTEventType,
    STTStreamOptions,
    VADSegmenter,
    pcm16_to_float32,
)

FIXTURE = Path(__file__).parent / "fixtures" / "speech_pause_speech_16k.wav"


class FakeSTTBackend:
    """Batch STT backend that records the audio it gets."""

    def __init__(self, model_size: str = "turbo", delay_s: float = 0.0, text: str = "fake text"):
        self.model = None
        self.model_size = model_size
        self.delay_s = delay_s
        self.text = text
        self.calls: list[tuple[int, str | None, str | None]] = []  # (samples, language, model_size)

    def is_loaded(self) -> bool:
        return self.model is not None

    def _is_model_cached(self, model_size: str) -> bool:
        return True

    async def load_model(self, model_size: str | None = None) -> None:
        self.model = object()
        self.model_size = model_size or self.model_size

    async def transcribe(self, audio_path, language=None, model_size=None) -> str:
        await self.load_model(model_size)
        audio, sr = sf.read(audio_path, dtype="float32")
        assert sr == STREAM_SAMPLE_RATE
        self.calls.append((len(audio), language, model_size))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return f"{self.text} {len(self.calls)}"

    def unload_model(self) -> None:
        self.model = None


WHISPER_TURBO = backends.ModelConfig(
    model_name="whisper-turbo",
    display_name="Whisper Turbo",
    engine="whisper",
    hf_repo_id="openai/whisper-large-v3-turbo",
    model_size="turbo",
)


async def _open(backend=None, batch_interim=False, lock=None, **opts) -> tuple[StreamSession, FakeSTTBackend]:
    backend = backend or FakeSTTBackend()
    options = STTStreamOptions(model="whisper-turbo", **{"vad": False, **opts})
    adapter = SegmentedBatchStream(backend, WHISPER_TURBO, options.language, interim=batch_interim)
    session = StreamSession(adapter, options, lock=lock or asyncio.Lock())
    await session.start()
    return session, backend


async def _close_and_collect(session: StreamSession) -> list:
    await session.aclose()
    return [e async for e in session.events()]


def _tone(seconds: float, sr: int = STREAM_SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


async def _push_frames(session: StreamSession, audio: np.ndarray, frame: int, realtime_sr: int | None = None):
    for i in range(0, len(audio), frame):
        await session.push_audio(audio[i : i + frame])
        if realtime_sr:
            await asyncio.sleep(frame / realtime_sr)


# ── SegmentedBatchStream ─────────────────────────────────────────────────────


async def test_segmented_end_segment_gives_one_final():
    session, backend = await _open(language="en")
    await _push_frames(session, _tone(1.0), frame=320)
    await session.end_segment()
    events = await _close_and_collect(session)

    finals = [e for e in events if e.type == STTEventType.FINAL]
    assert len(finals) == 1
    assert finals[0].text == "fake text 1"
    assert finals[0].language == "en"
    assert finals[0].start_s == 0.0
    assert finals[0].end_s == pytest.approx(1.0)
    assert backend.calls == [(16000, "en", "turbo")]
    # With vad off there are no VAD events.
    assert not [e for e in events if e.type != STTEventType.FINAL]


async def test_segmented_capabilities_are_final_only():
    session, _ = await _open()
    caps = session.capabilities
    assert caps.interim_results is False
    assert caps.native_streaming is False
    assert caps.needs_vad is True
    await session.aclose()


async def test_cancel_segment_gives_no_final():
    session, backend = await _open()
    await _push_frames(session, _tone(0.5), frame=320)
    await session.cancel_segment()
    events = await _close_and_collect(session)
    assert events == []
    assert backend.calls == []


async def test_segments_after_cancel_get_new_ids():
    session, _ = await _open()
    await session.push_audio(_tone(0.3))
    await session.cancel_segment()
    await session.push_audio(_tone(0.3))
    await session.end_segment()
    await session.push_audio(_tone(0.3))
    events = await _close_and_collect(session)  # aclose() finalizes the open segment
    assert [(e.type, e.segment_id) for e in events] == [(STTEventType.FINAL, 2), (STTEventType.FINAL, 3)]


async def test_end_segment_with_no_audio_gives_no_final():
    session, backend = await _open()
    await session.end_segment()
    assert await _close_and_collect(session) == []
    assert backend.calls == []


async def test_max_segment_length_forces_final():
    session, backend = await _open(max_segment_s=1.0)
    await _push_frames(session, _tone(2.5), frame=1600)
    events = await _close_and_collect(session)
    finals = [e for e in events if e.type == STTEventType.FINAL]
    assert [c[0] for c in backend.calls] == [16000, 16000, 8000]
    assert [(f.start_s, f.end_s) for f in finals] == [(0.0, 1.0), (1.0, 2.0), (2.0, 2.5)]


async def test_model_step_holds_inference_lock():
    lock = asyncio.Lock()
    session, backend = await _open(lock=lock)
    await session.push_audio(_tone(0.5))
    async with lock:  # e.g. a TTS chunk is running
        await session.end_segment()
        await asyncio.sleep(0.05)
        assert backend.calls == []
    events = await _close_and_collect(session)
    assert [e.type for e in events] == [STTEventType.FINAL]


async def test_step_error_gives_error_event_and_stream_continues():
    class Failing(FakeSTTBackend):
        async def transcribe(self, audio_path, language=None, model_size=None):
            if not self.calls:
                self.calls.append((0, None, None))
                raise RuntimeError("boom")
            return await super().transcribe(audio_path, language, model_size)

    session, _ = await _open(backend=Failing())
    await session.push_audio(_tone(0.3))
    await session.end_segment()
    await session.push_audio(_tone(0.3))
    events = await _close_and_collect(session)
    assert [e.type for e in events] == [STTEventType.ERROR, STTEventType.FINAL]
    assert events[0].message == "boom"


# ── Resampling ───────────────────────────────────────────────────────────────


def test_resampler_48k_gives_16k_sample_count():
    r = Resampler(48000)
    audio = _tone(1.0, sr=48000)
    out = [r.process(audio[i : i + 960]) for i in range(0, len(audio), 960)]
    out.append(r.flush())
    assert sum(len(x) for x in out) == 16000


async def test_session_resamples_48k_to_16k():
    session, backend = await _open(sample_rate=48000)
    await _push_frames(session, _tone(1.0, sr=48000), frame=960)  # 20 ms frames
    await session.end_segment()
    await _close_and_collect(session)
    assert backend.calls[0][0] == 16000


def test_pcm16_to_float32():
    pcm = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()
    out = pcm16_to_float32(pcm + b"\x01")  # an odd trailing byte is dropped
    np.testing.assert_allclose(out, [0.0, 0.5, -1.0, 32767 / 32768])
    assert out.dtype == np.float32


# ── Interim ticks ────────────────────────────────────────────────────────────


async def test_slow_model_skips_interim_ticks_but_gets_all_audio():
    backend = FakeSTTBackend(delay_s=0.2)
    session, _ = await _open(backend=backend, batch_interim=True, interim_interval_ms=50)
    assert session.capabilities.interim_results is True

    await _push_frames(session, _tone(1.0), frame=320, realtime_sr=STREAM_SAMPLE_RATE)
    await session.end_segment()
    events = await _close_and_collect(session)

    interim_calls = len(backend.calls) - 1
    assert session.ticks >= 10
    assert session.skipped_ticks > 0
    assert 1 <= interim_calls < session.ticks
    # The FINAL step got the complete segment.
    assert backend.calls[-1][0] == 16000
    assert events[-1].type == STTEventType.FINAL
    assert all(e.type == STTEventType.INTERIM for e in events[:-1])


async def test_interim_disabled_by_options():
    backend = FakeSTTBackend()
    session, _ = await _open(backend=backend, batch_interim=True, interim_interval_ms=50, interim=False)
    await _push_frames(session, _tone(0.5), frame=320, realtime_sr=STREAM_SAMPLE_RATE)
    events = await _close_and_collect(session)
    assert [e.type for e in events] == [STTEventType.FINAL]
    assert len(backend.calls) == 1


# ── VAD ──────────────────────────────────────────────────────────────────────


def test_vad_segmenter_start_preroll_and_end():
    # 1 = speech frame, 0 = silence frame (32 ms each).
    pattern = [0] * 20 + [1] * 30 + [0] * 20
    probs = iter(float(p) for p in pattern)
    seg = VADSegmenter(lambda frame: next(probs))
    audio = np.zeros(len(pattern) * 512, dtype=np.float32)
    items = seg.process(audio)

    events = [i for i in items if isinstance(i, SegmenterEvent)]
    assert [e.kind for e in events] == ["start", "end"]
    # Speech starts at frame 20; the segment starts 300 ms (9 frames) earlier.
    assert events[0].sample == (20 - 9) * 512
    # 500 ms of silence = 16 frames after the last speech frame (frame 49).
    assert events[1].sample == (50 + 16) * 512
    seg_samples = sum(len(i) for i in items if isinstance(i, np.ndarray))
    assert seg_samples == events[1].sample - events[0].sample


def test_vad_segmenter_ignores_short_noise():
    pattern = [0] * 10 + [1] * 4 + [0] * 10  # 128 ms of "speech" is under start_ms
    probs = iter(float(p) for p in pattern)
    seg = VADSegmenter(lambda frame: next(probs))
    assert seg.process(np.zeros(len(pattern) * 512, dtype=np.float32)) == []


def _fixture_pcm16() -> bytes:
    audio, sr = sf.read(FIXTURE, dtype="int16")
    assert sr == 16000
    return audio.tobytes()


async def test_silero_vad_speech_silence_speech_gives_two_segments():
    backend = FakeSTTBackend()
    session, _ = await _open(backend=backend, vad=True)
    assert isinstance(session.vad.prob_fn, SileroVAD)
    pcm = _fixture_pcm16()
    for i in range(0, len(pcm), 640):  # 20 ms frames
        await session.push_pcm16(pcm[i : i + 640])
    events = await _close_and_collect(session)

    # The model runs in the background, so FINAL 1 can come before or after
    # SPEECH_START 2. Inside each segment the order is fixed.
    assert len(events) == 6
    for seg_id in (1, 2):
        assert [e.type for e in events if e.segment_id == seg_id] == [
            STTEventType.SPEECH_START,
            STTEventType.SPEECH_END,
            STTEventType.FINAL,
        ]
    starts = [e.start_s for e in events if e.type == STTEventType.SPEECH_START]
    ends = [e.end_s for e in events if e.type == STTEventType.SPEECH_END]
    # Speech 1 is at 0.5-3.3 s, speech 2 at 4.8-7.1 s (see the fixture).
    assert 0.0 <= starts[0] < 0.8
    assert 3.0 < ends[0] < 4.6
    assert 4.0 < starts[1] < 5.2
    assert ends[1] > 7.0
    assert len(backend.calls) == 2


# ── Registry ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mlx_registry(monkeypatch):
    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    backends.reset_backends()
    whisper, qwen = FakeSTTBackend("turbo"), FakeSTTBackend("0.6B")
    backends._stt_backends["whisper"] = whisper
    backends._stt_backends["qwen_asr"] = qwen
    yield whisper, qwen
    backends.reset_backends()


@pytest.mark.parametrize(
    ("model", "engine", "model_size"),
    [("turbo", "whisper", "turbo"), ("whisper-base", "whisper", "base"), ("qwen-asr-0.6B", "qwen_asr", "0.6B")],
)
async def test_open_stt_stream_uses_segmented_adapter_for_every_model(mlx_registry, model, engine, model_size):
    session = backends.open_stt_stream(STTStreamOptions(model=model, vad=False), lock=asyncio.Lock())
    assert isinstance(session.adapter, SegmentedBatchStream)
    assert session.adapter.config.engine == engine
    assert session.adapter.config.stream_adapter == "segmented"
    await session.start()
    await session.push_audio(_tone(0.2))
    events = await _close_and_collect(session)
    assert [e.type for e in events] == [STTEventType.FINAL]
    assert backends._stt_backends[engine].calls[0][2] == model_size


def test_open_stt_stream_rejects_unknown_model(mlx_registry):
    with pytest.raises(ValueError, match="Unknown STT model"):
        backends.open_stt_stream(STTStreamOptions(model="bogus"))
