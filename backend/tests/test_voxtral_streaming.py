"""
Tests for the native streaming adapters (phase 5).

- ``NativeStreamingStream`` with a fake model: only new audio goes to
  ``feed()``, INTERIM only when the text grows, ``flush()`` at FINAL, the
  80 ms tick, and the registry entry.
- ``VoxtralNativeStream`` with the real Voxtral Realtime model (slow, needs the
  model in the Hugging Face cache; set ``VOICEBOX_RUN_SLOW=1``):

      VOICEBOX_RUN_SLOW=1 backend/venv/bin/python -m pytest backend/tests/test_voxtral_streaming.py -v -s

  The streamed mel equals batch mode, the FINAL text equals batch text (same
  delay), the interim text is append-only, and the median word latency is
  < 0.7 s.
"""

import asyncio
import itertools
import os
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend import backends
from backend.backends.stt_streaming import (
    STREAM_SAMPLE_RATE,
    NativeStreamingStream,
    StreamSession,
    STTEventType,
    STTStreamOptions,
    run_blocking,
)

FIXTURE = Path(__file__).parent / "fixtures" / "speech_pause_speech_16k.wav"
# Speech onsets in the fixture (seconds), as in test_qwen_asr_streaming.py.
FIXTURE_ONSETS = (0.5, 4.8)
# Word end times in the fixture (seconds), from Whisper large-v3-turbo word
# timestamps (alignment heads from its generation_config.json).
FIXTURE_WORD_ENDS = (
    (0.78, 1.38, 1.52, 1.66, 1.92, 2.30, 2.60, 2.74, 3.16),  # Hello, this is the first sentence for the test.
    (4.88, 5.08, 5.26, 5.58, 5.88, 6.04, 6.16, 6.44, 6.80),  # And now the second part of the speech arrives.
)


# ── NativeStreamingStream with a fake model ──────────────────────────────────


class FakeNative(NativeStreamingStream):
    """Fake model: 1 word for each 1600 samples (100 ms) it gets."""

    def __init__(self):
        super().__init__("fake-native")
        self.fed: list[int] = []  # sample counts given to feed()
        self.samples = 0
        self.begun = 0
        self.flushed = 0
        self.freed = 0

    def begin_segment(self) -> None:
        self.begun += 1
        self.samples = 0

    def feed(self, audio: np.ndarray) -> str:
        self.fed.append(len(audio))
        self.samples += len(audio)
        return self._words(self.samples // 1600)

    def flush(self) -> str:
        self.flushed += 1
        return self._words(self.samples // 1600 + 1)

    def free_segment(self) -> None:
        self.freed += 1

    @staticmethod
    def _words(n: int) -> str:
        return " ".join(f"w{i}" for i in range(n))


def _silence(samples: int) -> np.ndarray:
    return np.zeros(samples, dtype=np.float32)


async def test_native_feed_gets_only_new_audio():
    adapter = FakeNative()
    await adapter.interim(_silence(1000), 1)
    await adapter.interim(_silence(3500), 1)
    await adapter.interim(_silence(3500), 1)  # no new audio
    assert adapter.fed == [1000, 2500, 0]
    assert adapter.begun == 1


async def test_native_interim_only_when_text_grows():
    adapter = FakeNative()
    assert await adapter.interim(_silence(1000), 1) is None  # no word yet
    assert await adapter.interim(_silence(1700), 1) == ("w0", "w0")
    assert await adapter.interim(_silence(1800), 1) is None  # same text
    assert await adapter.interim(_silence(3300), 1) == ("w0 w1", "w0 w1")


async def test_native_finalize_feeds_the_rest_then_flushes():
    adapter = FakeNative()
    await adapter.interim(_silence(1600), 1)
    final = await adapter.finalize(_silence(4000), 1)
    assert adapter.fed == [1600, 2400]
    assert adapter.flushed == 1
    assert final == "w0 w1 w2"


async def test_native_new_segment_and_reset_clear_state():
    adapter = FakeNative()
    await adapter.interim(_silence(3200), 1)
    await adapter.interim(_silence(1600), 2)  # new segment: begins again, all of its audio is new
    assert adapter.fed == [3200, 1600]
    assert adapter.begun == 2
    assert adapter.freed == 1
    adapter.reset(2)
    await run_blocking(adapter.model_name, lambda: None)  # wait for the queued clear
    assert adapter._segment_id is None
    assert adapter._consumed == 0
    assert adapter.freed == 2


async def test_session_ticks_every_80_ms_with_native_adapter():
    adapter = FakeNative()
    # The client interval is ignored: the native adapter steps every 80 ms.
    options = STTStreamOptions(model="fake", vad=False, interim_interval_ms=2000)
    session = StreamSession(adapter, options, lock=asyncio.Lock())
    await session.start()
    caps = session.capabilities
    assert (caps.interim_results, caps.revises_text, caps.native_streaming, caps.needs_vad) == (
        True,
        False,
        True,
        False,
    )
    frame = _silence(STREAM_SAMPLE_RATE // 50)
    for _ in range(40):  # 0.8 s
        await session.push_audio(frame)
        await asyncio.sleep(0.02)
    await session.aclose()
    events = [e async for e in session.events()]
    interims = [e for e in events if e.type == STTEventType.INTERIM]
    assert len(interims) >= 4
    for before, after in itertools.pairwise(interims):
        assert after.text.startswith(before.text)  # append-only
        assert after.text != before.text
    assert events[-1].type == STTEventType.FINAL
    assert events[-1].text.startswith(interims[-1].text)


class BacklogNative(FakeNative):
    """Fake model that processes at most 1600 samples per feed() call."""

    def __init__(self):
        super().__init__()
        self.pending = 0

    def feed(self, audio: np.ndarray) -> str:
        self.fed.append(len(audio))
        self.pending += len(audio)
        done = min(self.pending, 1600)
        self.pending -= done
        self.samples += done
        return self._words(self.samples // 1600)

    def has_backlog(self) -> bool:
        return self.pending > 0

    def flush(self) -> str:
        assert self.pending == 0, "catch_up() must process the backlog before flush()"
        return super().flush()


class CountingLock(asyncio.Lock):
    def __init__(self):
        super().__init__()
        self.holds = 0

    async def acquire(self):
        self.holds += 1
        return await super().acquire()


async def test_final_catches_up_in_short_lock_holds():
    adapter = BacklogNative()
    lock = CountingLock()
    session = StreamSession(adapter, STTStreamOptions(model="fake", vad=False, interim=False), lock=lock)
    await session.start()
    await session.push_audio(_silence(8000))  # 5 feed() calls of work
    holds_before = lock.holds
    await session.aclose()
    events = [e async for e in session.events()]
    assert [e.type for e in events] == [STTEventType.FINAL]
    assert events[0].text == "w0 w1 w2 w3 w4 w5"
    assert adapter.fed == [8000, 0, 0, 0, 0, 0]  # 5 catch-up steps, then finalize
    assert lock.holds - holds_before == 6  # 5 catch-up steps, then finalize


# ── Registry ─────────────────────────────────────────────────────────────────


def test_open_stt_stream_uses_native_adapter_for_voxtral(monkeypatch):
    from backend.backends.voxtral_streaming import VoxtralNativeStream

    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    backends.reset_backends()
    try:
        session = backends.open_stt_stream(
            STTStreamOptions(model="voxtral-realtime-4B", vad=False), lock=asyncio.Lock()
        )
        assert isinstance(session.adapter, VoxtralNativeStream)
        assert session.adapter.delay_ms == 240  # DEFAULT_DELAY_MS
        other = backends.open_stt_stream(
            STTStreamOptions(model="voxtral-realtime-4B", vad=False, delay_ms=480), lock=asyncio.Lock()
        )
        assert other.adapter.delay_ms == 480
        assert session.adapter.config.stream_adapter == "native"
        assert session.adapter.config.engine == "voxtral_realtime"
        assert session.capabilities.native_streaming is True
        assert session.capabilities.revises_text is False
    finally:
        backends.reset_backends()


def test_voxtral_is_mlx_only(monkeypatch):
    monkeypatch.setattr(backends, "get_backend_type", lambda: "pytorch")
    assert not backends.is_valid_stt_model("voxtral-realtime-4B")
    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    assert backends.is_valid_stt_model("voxtral-realtime-4B")


# ── VoxtralNativeStream with the real model ──────────────────────────────────

slow = pytest.mark.skipif(
    os.environ.get("VOICEBOX_RUN_SLOW") != "1",
    reason="runs the real Voxtral Realtime model; set VOICEBOX_RUN_SLOW=1 to run",
)


@pytest.fixture
def voxtral_backend(monkeypatch):
    from backend.backends.voxtral_realtime_backend import MLXVoxtralRealtimeBackend

    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    config = backends.resolve_stt_model("voxtral-realtime-4B")
    backend = MLXVoxtralRealtimeBackend(config.model_size)
    if not backend._is_model_cached(config.model_size):
        pytest.skip(f"{config.hf_repo_id} is not in the Hugging Face cache")
    return backend, config


def _fixture_audio() -> np.ndarray:
    audio, sr = sf.read(FIXTURE, dtype="float32")
    assert sr == STREAM_SAMPLE_RATE
    return audio


def _norm(word: str) -> str:
    return word.strip(".,!?").lower()


def word_latencies(texts: list[tuple[float, str]], final: str, word_ends: tuple[float, ...], t0: float) -> list[float]:
    """Time from the end of each word's audio to the first text that has the complete word.

    ``texts`` are (arrival time, text) in order.
    """
    target = [_norm(w) for w in final.split()]
    assert len(target) == len(word_ends), final
    latencies = []
    for k, end in enumerate(word_ends):
        for t, text in texts:
            words = [_norm(w) for w in text.split()]
            if len(words) > k and words[k] == target[k]:
                latencies.append(t - (t0 + end))
                break
    return latencies


async def _batch_text(adapter, audio: np.ndarray) -> str:
    """Batch-mode text with the delay of the stream (``POST /transcribe`` uses the model default)."""

    def generate():
        model = adapter.backend.model
        try:
            return model.generate(audio, transcription_delay_ms=adapter.delay_ms).text.strip()
        finally:
            model._ensure_ada_scales(None)

    return await run_blocking(adapter.model_name, generate)


@slow
async def test_streamed_mel_equals_batch_mode(voxtral_backend):
    import mlx.core as mx

    from backend.backends.voxtral_streaming import SAMPLES_PER_TOKEN, VoxtralNativeStream

    backend, config = voxtral_backend
    adapter = VoxtralNativeStream(backend, config)
    await adapter.prepare()
    audio = _fixture_audio()[: 3 * STREAM_SAMPLE_RATE]

    def batch():
        mel, _ = adapter._model._prepare_mel(audio)
        return np.array(mel.T)

    def streamed():
        adapter._bind_model()
        adapter._state = adapter._new_state()
        adapter._state["audio"].append(audio)
        adapter._state["samples"] += len(audio)
        n_tokens = adapter._state["samples"] // SAMPLES_PER_TOKEN
        # 3 calls, as steps would make them.
        parts = [adapter._log_mel(a * 8, b * 8) for a, b in ((0, 5), (5, 31), (31, n_tokens - 1))]
        return np.array(mx.concatenate(parts, axis=0))

    batch_mel = await run_blocking(adapter.model_name, batch)
    mel = await run_blocking(adapter.model_name, streamed)
    assert np.abs(mel - batch_mel[: mel.shape[0]]).max() < 1e-4


@slow
async def test_steps_final_equals_batch_and_text_is_append_only(voxtral_backend):
    from backend.backends.voxtral_streaming import VoxtralNativeStream

    backend, config = voxtral_backend
    adapter = VoxtralNativeStream(backend, config)
    await adapter.prepare()
    audio = _fixture_audio()

    texts = []
    times = []
    step = STREAM_SAMPLE_RATE * 80 // 1000
    for end in range(step, len(audio) + 1, step):
        started = time.perf_counter()
        result = await adapter.interim(audio[:end], 1)
        times.append((time.perf_counter() - started) * 1000)
        if result is not None:
            texts.append(result[0])
    final = await adapter.finalize(audio, 1)
    adapter.reset(1)
    times.sort()
    print(f"\n{len(times)} steps: p50 {times[len(times) // 2]:.0f} ms, max {times[-1]:.0f} ms")
    print(f"interim: {texts[-1]!r}\nfinal:   {final!r}")

    for before, after in itertools.pairwise(texts):
        assert after.startswith(before)
    assert final.startswith(texts[-1])
    assert final == await _batch_text(adapter, audio)


@slow
async def test_realtime_session_text_latency(voxtral_backend):
    """The fixture at real-time speed through StreamSession with the server VAD."""
    from backend.backends.voxtral_streaming import VoxtralNativeStream

    backend, config = voxtral_backend
    adapter = VoxtralNativeStream(backend, config)
    segment_audio: dict[int, np.ndarray] = {}
    finalize = adapter.finalize

    async def recording_finalize(audio, segment_id):
        segment_audio[segment_id] = audio.copy()
        return await finalize(audio, segment_id)

    adapter.finalize = recording_finalize
    session = StreamSession(adapter, STTStreamOptions(model=config.model_name), lock=asyncio.Lock())
    await session.start()

    events: list[tuple[float, object]] = []

    async def collect():
        async for event in session.events():
            events.append((time.perf_counter(), event))

    collector = asyncio.create_task(collect())
    fixture = _fixture_audio()
    audio = np.concatenate([fixture, np.zeros(STREAM_SAMPLE_RATE, dtype=np.float32)])
    frame = STREAM_SAMPLE_RATE // 50  # 20 ms
    t0 = time.perf_counter()
    for i in range(0, len(audio), frame):
        await asyncio.sleep(max(0.0, t0 + i / STREAM_SAMPLE_RATE - time.perf_counter()))
        await session.push_audio(audio[i : i + frame])
    await session.aclose()
    await collector

    finals = {e.segment_id: (t, e) for t, e in events if e.type == STTEventType.FINAL}
    assert sorted(finals) == [1, 2]
    all_latencies = []
    for seg, onset, word_ends in zip((1, 2), FIXTURE_ONSETS, FIXTURE_WORD_ENDS, strict=True):
        interims = [(t, e) for t, e in events if e.type == STTEventType.INTERIM and e.segment_id == seg]
        final_t, final = finals[seg]
        for (_, before), (_, after) in itertools.pairwise(interims):
            assert after.text.startswith(before.text)  # append-only
        assert final.text.startswith(interims[-1][1].text)
        assert final.text == await _batch_text(adapter, segment_audio[seg])
        first = next(t for t, e in interims if e.text) - (t0 + onset)
        texts = [(t, e.text) for t, e in interims] + [(final_t, final.text)]
        latencies = word_latencies(texts, final.text, word_ends, t0)
        print(
            f"segment {seg}: {len(interims)} interims, first text {first:.2f} s after the onset; "
            f"word latency {' '.join(f'{x:.2f}' for x in latencies)} s; "
            f"FINAL {final_t - (t0 + word_ends[-1]):.2f} s after the speech end; {final.text!r}"
        )
        all_latencies += latencies[:-1]  # the last word comes with FINAL (VAD end + flush)
    all_latencies.sort()
    p50 = all_latencies[len(all_latencies) // 2]
    print(f"word latency (all but the last word): p50 {p50:.2f} s, max {all_latencies[-1]:.2f} s")
    assert p50 < 0.7
    assert all_latencies[-1] < 1.0
