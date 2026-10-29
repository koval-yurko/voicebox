"""
Tests for the rolling re-decode adapters (phase 4).

- ``RollingRedecodeStream`` with a fake model: LocalAgreement-2, the rollback
  prefix, and ``stable_text`` that never gets shorter.
- ``QwenASRRollingStream`` with the real Qwen3-ASR model (slow, needs the
  model in the Hugging Face cache; set ``VOICEBOX_RUN_SLOW=1``):

      VOICEBOX_RUN_SLOW=1 backend/venv/bin/python -m pytest backend/tests/test_qwen_asr_streaming.py -v -s

  The window mel and encoder output equal batch mode, the FINAL text equals
  batch text, interim text arrives < 1.2 s after the speech starts, and the
  step times are logged.
"""

import asyncio
import itertools
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.backends.stt_streaming import (
    STREAM_SAMPLE_RATE,
    RollingRedecodeStream,
    StreamSession,
    STTEventType,
    STTStreamOptions,
    run_blocking,
)

FIXTURE = Path(__file__).parent / "fixtures" / "speech_pause_speech_16k.wav"
# Speech onsets in the fixture (seconds): "Hello, this is the first sentence
# for the test." and "And now the second part of the speech arrives."
FIXTURE_ONSETS = (0.5, 4.8)


# ── RollingRedecodeStream with a fake model ──────────────────────────────────


class ScriptedRolling(RollingRedecodeStream):
    """Fake model: each step returns the next scripted hypothesis.

    Token ids are words. ``decode()`` records the forced prefix and returns the
    words of the hypothesis after it.
    """

    def __init__(self, hypotheses: list[str]):
        super().__init__("fake-rolling")
        self.hypotheses = list(hypotheses)
        self.prefixes: list[list[str]] = []
        self.vocab: list[str] = []
        self.audio_lengths: list[int] = []
        self.freed = 0

    def _id(self, word: str) -> int:
        if word not in self.vocab:
            self.vocab.append(word)
        return self.vocab.index(word)

    def encode_incremental(self, audio: np.ndarray) -> None:
        self.audio_lengths.append(len(audio))

    def decode(self, prefix_ids: list[int], max_new: int) -> list[int]:
        prefix = [self.vocab[i] for i in prefix_ids]
        self.prefixes.append(prefix)
        words = self.hypotheses.pop(0).split() if self.hypotheses else []
        return [self._id(w) for w in words[len(prefix) :][:max_new]]

    def encode_text(self, text: str) -> list[int]:
        return [self._id(w) for w in text.split()]

    def decode_text(self, ids: list[int]) -> str:
        return " ".join(self.vocab[i] for i in ids)

    def free_segment(self) -> None:
        self.freed += 1

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        return "final text"


def _run_steps(adapter: ScriptedRolling, n: int, segment_id: int = 1) -> list[tuple[str, str]]:
    return [adapter._interim_step(np.zeros(1600 * (i + 1), dtype=np.float32), segment_id) for i in range(n)]


def test_local_agreement_commits_words_two_steps_agree_on():
    adapter = ScriptedRolling(["the cat", "the cat sat on", "the cat sat on the mat"])
    results = _run_steps(adapter, 3)
    # Step 1: nothing to compare with. Step 2: "the" agrees (the last word
    # "cat" of step 1 is not complete yet). Step 3: "the cat sat" agrees.
    assert [stable for _, stable in results] == ["", "the", "the cat sat"]
    assert [text for text, _ in results] == ["the cat", "the cat sat on", "the cat sat on the mat"]


def test_last_word_is_never_committed():
    adapter = ScriptedRolling(["hello wor", "hello wor"])
    assert _run_steps(adapter, 2)[-1] == ("hello wor", "hello")


def test_rollback_prefix_is_committed_tokens_minus_five():
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
    adapter = ScriptedRolling([" ".join(words)] * 3)
    _run_steps(adapter, 3)
    assert adapter.prefixes[0] == []
    assert adapter.prefixes[1] == []
    # After step 2, 9 words are committed (all but the last): prefix = 9 - 5 tokens.
    assert adapter.prefixes[2] == words[:4]


def test_decode_gets_at_most_24_new_tokens():
    adapter = ScriptedRolling([" ".join(f"w{i}" for i in range(40))])
    text, _ = _run_steps(adapter, 1)[0]
    assert len(text.split()) == 24


def test_stable_text_never_gets_shorter_when_hypothesis_changes():
    adapter = ScriptedRolling(
        [
            "mr smith went",
            "mr smith went to",
            "mister smith went to the",  # the rolled-back part decodes differently
            "",  # empty hypothesis
            "mister smith goes to the shop today",
        ]
    )
    results = _run_steps(adapter, 5)
    stables = [stable for _, stable in results]
    for before, after in itertools.pairwise(stables):
        assert after.startswith(before)
    for text, stable in results:
        assert text.startswith(stable)
    assert stables[-1].startswith("mr smith went")


def test_new_segment_starts_with_empty_state():
    adapter = ScriptedRolling(["a b c", "a b c", "x y", "x y z"])
    _run_steps(adapter, 2, segment_id=1)
    results = _run_steps(adapter, 2, segment_id=2)
    assert results == [("x y", ""), ("x y z", "x")]
    assert adapter.freed == 1


async def test_reset_clears_state_on_the_worker_thread():
    adapter = ScriptedRolling(["a b c", "a b c"])
    await adapter.interim(np.zeros(1600, dtype=np.float32), 1)
    await adapter.interim(np.zeros(3200, dtype=np.float32), 1)
    assert adapter._committed == ["a", "b"]
    adapter.reset(1)
    await run_blocking(adapter.model_name, lambda: None)  # wait for the queued clear
    assert adapter._committed == []
    assert adapter._segment_id is None
    assert adapter.freed == 1


async def test_session_emits_interim_then_final_with_rolling_adapter():
    adapter = ScriptedRolling([f"word{i} " * (i + 1) for i in range(100)])
    session = StreamSession(
        adapter, STTStreamOptions(model="fake", vad=False, interim_interval_ms=20), lock=asyncio.Lock()
    )
    await session.start()
    assert session.capabilities.interim_results is True
    t = np.arange(STREAM_SAMPLE_RATE // 50) / STREAM_SAMPLE_RATE
    frame = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for _ in range(15):
        await session.push_audio(frame)
        await asyncio.sleep(0.02)
    await session.aclose()
    events = [e async for e in session.events()]
    kinds = [e.type for e in events]
    assert STTEventType.INTERIM in kinds
    assert kinds[-1] == STTEventType.FINAL
    assert events[-1].text == "final text"
    # The adapter got the whole open segment, growing, at each step.
    assert adapter.audio_lengths == sorted(adapter.audio_lengths)


# ── QwenASRRollingStream with the real model ─────────────────────────────────

slow = pytest.mark.skipif(
    os.environ.get("VOICEBOX_RUN_SLOW") != "1",
    reason="runs the real Qwen3-ASR model; set VOICEBOX_RUN_SLOW=1 to run",
)
MODELS = [m.strip() for m in os.environ.get("VOICEBOX_QWEN_ASR_MODELS", "qwen-asr-0.6B").split(",")]


@pytest.fixture(params=MODELS)
def qwen_backend(request, monkeypatch):
    # The batch path (mlx_lm generate_step) uses a module-level MLX stream that
    # belongs to the thread that imported mlx_lm.generate, and MLX streams are
    # per thread. Each test has its own event loop and default executor, so
    # import the module again in this test's threads.
    monkeypatch.delitem(sys.modules, "mlx_lm.generate", raising=False)
    from backend import backends
    from backend.backends.qwen_asr_backend import MLXQwenASRBackend

    config = backends.resolve_stt_model(request.param)
    backend = MLXQwenASRBackend(config.model_size)
    if not backend._is_model_cached(config.model_size):
        pytest.skip(f"{config.hf_repo_id} is not in the Hugging Face cache")
    return backend, config


def _fixture_audio() -> np.ndarray:
    audio, sr = sf.read(FIXTURE, dtype="float32")
    assert sr == STREAM_SAMPLE_RATE
    return audio


def _long_clip() -> np.ndarray:
    """The fixture 3 times (23 s): 2 closed 8 s windows and 1 open window."""
    return np.concatenate([_fixture_audio()] * 3)


async def _batch_text(backend, config, audio: np.ndarray) -> str:
    from backend.backends.stt_streaming import _write_wav

    path = _write_wav(audio)
    try:
        return await backend.transcribe(path, "en", config.model_size)
    finally:
        os.unlink(path)


def _log_steps(name: str, steps: list[tuple[float, float]]) -> None:
    times = sorted(ms for _, ms in steps)
    print(f"\n{name}: {len(times)} interim steps")
    for audio_s, ms in steps:
        print(f"  audio {audio_s:5.1f} s  step {ms:5.0f} ms")
    print(f"  p50 {times[len(times) // 2]:.0f} ms, p95 {times[int(len(times) * 0.95)]:.0f} ms, max {times[-1]:.0f} ms")


def _record_steps(adapter) -> list[tuple[float, float]]:
    """Wrap ``_interim_step`` to log (audio seconds, step ms)."""
    steps: list[tuple[float, float]] = []
    step = adapter._interim_step

    def timed(audio, segment_id):
        started = time.perf_counter()
        result = step(audio, segment_id)
        steps.append((len(audio) / STREAM_SAMPLE_RATE, (time.perf_counter() - started) * 1000))
        return result

    adapter._interim_step = timed
    return steps


@slow
async def test_window_mel_and_encoder_equal_batch_mode(qwen_backend):
    import mlx.core as mx

    from backend.backends.qwen_asr_streaming import CLOSE_SAMPLES, WINDOW_SAMPLES, QwenASRRollingStream

    backend, config = qwen_backend
    adapter = QwenASRRollingStream(backend, config, "en")
    await adapter.prepare()
    audio = _long_clip()

    def batch():
        model = adapter._model
        feats, mask, _ = model._preprocess_audio(audio)
        out = model.get_audio_features(feats, mask).astype(mx.float32)
        mx.eval(out)
        return np.array(feats[0]), np.array(out)

    def windowed():
        n_windows = -(-len(audio) // (8 * STREAM_SAMPLE_RATE))
        raws = [adapter._log_mel(audio, w) for w in range(n_windows)]
        # A window closes as soon as CLOSE_SAMPLES after its end exist: its
        # frames must already be final then.
        for w in range(n_windows - 1):
            closes_at = (w + 1) * WINDOW_SAMPLES + CLOSE_SAMPLES
            assert np.array_equal(adapter._log_mel(audio[:closes_at], w), raws[w])
        mel_max = max(float(r.max()) for r in raws)
        mels = [adapter._normalize(r, mel_max) for r in raws]
        enc = [np.array(adapter._encode_window(m).astype(mx.float32)) for m in mels]
        return np.concatenate(mels, axis=1), np.concatenate(enc)

    batch_mel, batch_enc = await run_blocking(adapter.model_name, batch)
    mel, enc = await run_blocking(adapter.model_name, windowed)
    assert mel.shape == batch_mel.shape
    assert np.abs(mel - batch_mel).max() == 0.0
    assert enc.shape == batch_enc.shape
    assert np.abs(enc - batch_enc).max() < 1e-4


@slow
async def test_rolling_steps_on_long_clip_final_equals_batch(qwen_backend):
    """Steps every 0.5 s over 23 s: closed windows are cached, the prefix cache is trimmed."""
    from backend.backends.qwen_asr_streaming import QwenASRRollingStream

    backend, config = qwen_backend
    adapter = QwenASRRollingStream(backend, config, "en")
    await adapter.prepare()
    steps = _record_steps(adapter)
    audio = _long_clip()

    results = []
    for end in range(STREAM_SAMPLE_RATE // 2, len(audio) + 1, STREAM_SAMPLE_RATE // 2):
        results.append(await adapter.interim(audio[:end], 1))
        if end >= 17 * STREAM_SAMPLE_RATE:
            assert len(adapter._closed_feats) == 2  # 2 closed windows, encoded once each
    final = await adapter.finalize(audio, 1)
    adapter.reset(1)
    _log_steps(f"{config.model_name} long clip", steps)

    stables = [stable for _, stable in results]
    for before, after in itertools.pairwise(stables):
        assert after.startswith(before)
    assert results[-1][0].lower().count("second part of the speech") == 3
    assert final == await _batch_text(backend, config, audio)
    assert max(ms for _, ms in steps) < 500  # faster than the 500 ms interim interval


@slow
async def test_realtime_session_interim_latency_and_final_equals_batch(qwen_backend):
    """The fixture at real-time speed through StreamSession with the server VAD."""
    from backend.backends.qwen_asr_streaming import QwenASRRollingStream

    backend, config = qwen_backend
    adapter = QwenASRRollingStream(backend, config, "en")
    segment_audio: dict[int, np.ndarray] = {}
    finalize = adapter.finalize

    async def recording_finalize(audio, segment_id):
        segment_audio[segment_id] = audio.copy()
        return await finalize(audio, segment_id)

    adapter.finalize = recording_finalize
    steps = _record_steps(adapter)
    session = StreamSession(adapter, STTStreamOptions(model=config.model_name, language="en"), lock=asyncio.Lock())
    await session.start()

    events: list[tuple[float, object]] = []

    async def collect():
        async for event in session.events():
            events.append((time.perf_counter(), event))

    collector = asyncio.create_task(collect())
    audio = np.concatenate([_fixture_audio(), np.zeros(STREAM_SAMPLE_RATE, dtype=np.float32)])
    frame = STREAM_SAMPLE_RATE // 50  # 20 ms
    t0 = time.perf_counter()
    for i in range(0, len(audio), frame):
        # Real time: push each frame when its audio would be spoken.
        await asyncio.sleep(max(0.0, t0 + i / STREAM_SAMPLE_RATE - time.perf_counter()))
        await session.push_audio(audio[i : i + frame])
    await session.aclose()
    await collector
    _log_steps(f"{config.model_name} real time", steps)

    finals = [e for _, e in events if e.type == STTEventType.FINAL]
    assert len(finals) == 2
    for event in finals:
        assert event.text == await _batch_text(backend, config, segment_audio[event.segment_id])

    for seg, onset in zip((1, 2), FIXTURE_ONSETS, strict=True):
        interims = [(t, e) for t, e in events if e.type == STTEventType.INTERIM and e.segment_id == seg]
        first_text = next(t for t, e in interims if e.text.strip())
        latency = first_text - (t0 + onset)
        print(f"segment {seg}: first interim text {latency:.2f} s after the speech onset")
        assert latency < 1.2
        stables = [e.stable_text for _, e in interims]
        for before, after in itertools.pairwise(stables):
            assert after.startswith(before)
        assert all(e.text.startswith(e.stable_text) for _, e in interims)
