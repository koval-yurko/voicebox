"""
Voxtral Realtime native streaming adapter (design doc section 5.4).

Voxtral Mini 4B Realtime is built for streaming: the conv stem and the encoder
are causal, and the decoder takes 1 audio token per 80 ms and emits 1 text
token for it, with a fixed delay (``DEFAULT_DELAY_MS``, or ``delay_ms`` of
the stream). So the adapter keeps all state
between steps and runs only the new audio:

1. log-mel of the new frames only (the global clamp is fixed, so old frames
   never change);
2. the causal conv stem with the last input frames kept as a tail;
3. the encoder layers with their ``RotatingKVCache``s (sliding window 750);
4. 1 decoder step per new audio token, with the decoder cache kept.

The stream is the same as in ``Model.generate()``: 32 tokens of silence
before the audio, then the prompt ``[BOS] + [STREAMING_PAD] * (32 + delay)``. The state
after the silence tokens is the same for every segment, so it is computed
once and copied at each segment start. ``flush()`` adds (delay + 1) tokens of
silence after the audio, so the decoder gets to the last words.
Batch mode adds 10 more tokens, but the model emits no EOS and no more text
in them (checked on the test fixture), and they cost about 20 ms each.

This uses internals of mlx-audio 0.4.1 (``voxtral_realtime`` encoder and
decoder modules). Keep mlx-audio pinned; the model tests in
``backend/tests/test_voxtral_streaming.py`` compare against batch mode.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import numpy as np

from .stt_streaming import STREAM_SAMPLE_RATE, NativeStreamingStream, run_blocking

if TYPE_CHECKING:
    from . import ModelConfig, STTBackend

logger = logging.getLogger(__name__)

HOP = 160  # mel hop (10 ms)
N_FFT = 400
MEL_PER_TOKEN = 8  # 2x conv stride, 4x downsample
SAMPLES_PER_TOKEN = MEL_PER_TOKEN * HOP  # 1280 = 80 ms
# A mel frame centred at sample c uses samples up to c + N_FFT // 2, so the
# last frame of a token is final 40 samples after the token end.
LOOKAHEAD_SAMPLES = N_FFT // 2 - HOP
CONV0_TAIL = 2  # kernel 3, stride 1
CONV1_TAIL = 1  # kernel 3, stride 2
# Transcription delay. The model default is 480 ms, but then a word arrives
# about 0.8 s after its end (the model waits longer than the delay at word
# ends). 240 ms gives about 0.57 s; Fleurs WER (model card) goes from 8.7 %
# to 10.8 % on average, 4.9 % to 5.9 % for English.
DEFAULT_DELAY_MS = 240


class VoxtralNativeStream(NativeStreamingStream):
    """Append-only interim text from Voxtral Realtime, 1 decoder step per 80 ms."""

    # Decoder steps per interim or catch-up step (about 20 ms each). When a
    # TTS request held the GPU lock, the audio of that time waits; it is
    # decoded over several steps, so the next TTS request waits for 1 short
    # step only (a FINAL flush of the whole backlog held the lock for 0.5 s).
    max_decode_steps = 4

    def __init__(
        self,
        backend: STTBackend,
        config: ModelConfig,
        language: str | None = None,
        delay_ms: int | None = None,
    ):
        super().__init__(config.model_name)
        self.backend = backend
        self.config = config
        self.language = language  # Voxtral Realtime detects the language itself
        self.delay_ms = delay_ms or DEFAULT_DELAY_MS
        self._model: Any = None
        self._loaded: Any = None  # backend.model that _model belongs to
        self._silence: dict[str, Any] | None = None  # state after the left-pad silence
        self._state: dict[str, Any] | None = None

    # -- loading --

    async def prepare(self) -> None:
        await self._ensure_loaded()
        await run_blocking(self.model_name, self._warm_up)

    async def _ensure_loaded(self) -> None:
        from . import unload_other_stt_engines

        # Another request can unload or switch the model between steps.
        unload_other_stt_engines(self.config.engine)
        await self.backend.load_model(self.config.model_size)

    def _warm_up(self) -> None:
        """1 segment of silence, so the first real step has no first-run cost."""
        self._feed_new(np.zeros(STREAM_SAMPLE_RATE, dtype=np.float32), -1)
        self.flush()
        self.step_times_ms = []
        self._clear(-1)

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        await self._ensure_loaded()
        return await super().interim(audio, segment_id)

    async def catch_up(self, audio: np.ndarray, segment_id: int) -> bool:
        await self._ensure_loaded()
        return await super().catch_up(audio, segment_id)

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        await self._ensure_loaded()
        return await super().finalize(audio, segment_id)

    def _bind_model(self) -> None:
        """Use the backend's current model. Called on the worker thread."""
        loaded = self.backend.model
        if loaded is None:
            raise RuntimeError(f"{self.config.model_name} is not loaded")
        if loaded is self._loaded:
            return
        import mlx.core as mx
        from mlx_audio.stt.models.voxtral_realtime.voxtral_realtime import _num_delay_tokens

        model = loaded
        cfg = model.config
        self._n_left = cfg.n_left_pad_tokens
        self._n_delay = _num_delay_tokens(self.delay_ms)
        self._prompt_ids = [cfg.bos_token_id] + [cfg.streaming_pad_token_id] * (self._n_left + self._n_delay)
        self._eos_id = cfg.eos_token_id
        # Constants made on this thread (MLX streams are per thread).
        n = np.arange(N_FFT, dtype=np.float32)
        self._window = mx.array(0.5 * (1.0 - np.cos(2.0 * math.pi * n / N_FFT)))
        self._mel_filters = mx.array(np.array(model._ensure_mel_filters()))
        self._log_mel_min = cfg.audio_encoding_args.global_log_mel_max - 8.0
        self._model = model
        self._loaded = loaded
        self._silence = None
        self._state = None

    # -- NativeStreamingStream hooks --

    def begin_segment(self) -> None:
        self._bind_model()
        if self._silence is None:
            self._state = self._new_state()
            self._advance()  # only the left-pad silence is there: it stops before the audio
            self._silence = self._state
        self._state = self._copy_state(self._silence)

    def free_segment(self) -> None:
        self._state = None

    def feed(self, audio: np.ndarray) -> str:
        if self.backend.model is not self._loaded:
            # The model was reloaded after the segment started: run the
            # segment audio so far again on the new model.
            st = self._state
            earlier = np.concatenate(st["audio"])[self._n_left * SAMPLES_PER_TOKEN :]
            self.begin_segment()
            audio = np.concatenate([earlier, audio])
        if len(audio):
            self._state["audio"].append(audio.astype(np.float32))
            self._state["samples"] += len(audio)
        self._advance(self.max_decode_steps)
        return self._text_so_far()

    def has_backlog(self) -> bool:
        st = self._state
        return bool(st["adapter"]) and not st["done"]

    def flush(self) -> str:
        st = self._state
        # Silence as in batch mode, but only (delay + 1) tokens: align to a
        # token, the tokens, and the zeros the last frame sees (reflect padding).
        align = -st["samples"] % SAMPLES_PER_TOKEN
        pad = align + (self._n_delay + 1) * SAMPLES_PER_TOKEN + LOOKAHEAD_SAMPLES
        st["audio"].append(np.zeros(pad, dtype=np.float32))
        st["samples"] += pad
        self._advance()
        return self._text_so_far()

    # -- state --

    def _new_state(self) -> dict[str, Any]:
        import mlx.core as mx
        from mlx_lm.models.cache import RotatingKVCache

        enc = self._model.encoder
        n_mels = self._model.config.audio_encoding_args.num_mel_bins
        return {
            # Stream samples, with the left-pad silence first. Kept whole (a
            # segment is at most max_segment_s long).
            "audio": [np.zeros(self._n_left * SAMPLES_PER_TOKEN, dtype=np.float32)],
            "samples": self._n_left * SAMPLES_PER_TOKEN,
            "tokens": 0,  # audio tokens encoded
            "mel_tail": mx.zeros((CONV0_TAIL, n_mels), dtype=mx.float32),
            "x0_tail": mx.zeros((CONV1_TAIL, enc.config.dim)),
            "enc_caches": [RotatingKVCache(max_size=enc.config.sliding_window, keep=0) for _ in enc.transformer_layers],
            "adapter": [],  # encoded audio tokens not yet given to the decoder
            "pos": 0,  # next decoder position
            "dec_cache": None,
            "generated": [],
            "done": False,  # EOS seen
        }

    def _copy_state(self, src: dict[str, Any]) -> dict[str, Any]:
        """Copy a state. MLX slice assignment changes the array object, so the
        encoder caches get new arrays; the other arrays are never changed in place."""
        import mlx.core as mx
        from mlx_lm.models.cache import RotatingKVCache

        caches = []
        for c in src["enc_caches"]:
            new = RotatingKVCache(max_size=c.max_size, keep=c.keep)
            if c.keys is not None:
                new.keys = mx.array(c.keys)
                new.values = mx.array(c.values)
            new.offset = c.offset
            new._idx = c._idx
            caches.append(new)
        return {
            **src,
            "audio": list(src["audio"]),
            "enc_caches": caches,
            "adapter": list(src["adapter"]),
            "dec_cache": list(src["dec_cache"]) if src["dec_cache"] is not None else None,
            "generated": list(src["generated"]),
        }

    def _text_so_far(self) -> str:
        ids = [t for t in self._state["generated"] if t != self._eos_id]
        return self._model._tokenizer.decode(ids).strip()

    # -- model steps --

    def _advance(self, max_steps: int | None = None) -> None:
        """Encode the audio tokens whose frames are final, then run the decoder on them.

        ``max_steps`` limits the 1-token decoder steps; the rest waits for the next call.
        """
        st = self._state
        # The decoder norms depend on the delay. The batch path sets them back
        # to the model default, so check them at each step (cheap when equal).
        self._model._ensure_ada_scales(self.delay_ms)
        available = (st["samples"] - LOOKAHEAD_SAMPLES) // SAMPLES_PER_TOKEN
        if available > st["tokens"]:
            st["adapter"].append(self._encode(st["tokens"], available))
            st["tokens"] = available
        self._decode(max_steps)

    def _encode(self, first: int, end: int) -> Any:
        """Adapter output for audio tokens [first, end)."""
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_audio.stt.models.voxtral_realtime.encoder import _compute_rope_freqs

        st = self._state
        enc = self._model.encoder
        mel = self._log_mel(first * MEL_PER_TOKEN, end * MEL_PER_TOKEN)  # [frames, 128]

        # Causal conv stem: the kept tail replaces the left padding.
        x = mx.concatenate([st["mel_tail"], mel], axis=0)
        st["mel_tail"] = mel[-CONV0_TAIL:]
        x0 = nn.gelu(enc.conv_layers_0_conv.conv(x[None]))[0]
        x = mx.concatenate([st["x0_tail"].astype(x0.dtype), x0], axis=0)
        st["x0_tail"] = x0[-CONV1_TAIL:]
        x = nn.gelu(enc.conv_layers_1_conv.conv(x[None]))[0]  # [4 * (end - first), dim]

        n = x.shape[0]
        start = first * MEL_PER_TOKEN // 2
        cos, sin = _compute_rope_freqs(mx.arange(start, start + n), enc.config.head_dim, enc.config.rope_theta)
        for layer, cache in zip(enc.transformer_layers, st["enc_caches"], strict=True):
            mask = cache.make_mask(n, window_size=enc.config.sliding_window)
            x = layer(x, cos, sin, mask, cache=cache)
        out = enc.downsample_and_project(enc.transformer_norm(x))
        mx.eval(out, *[c.keys for c in st["enc_caches"]], *[c.values for c in st["enc_caches"]])
        return out

    def _log_mel(self, first: int, end: int) -> Any:
        """Log-mel frames [first, end) of the stream, as ``compute_mel_spectrogram`` gives them."""
        import mlx.core as mx

        st = self._state
        if len(st["audio"]) > 1:
            st["audio"] = [np.concatenate(st["audio"])]
        audio = st["audio"][0]
        half = N_FFT // 2
        lo = first * HOP - half
        hi = (end - 1) * HOP + half
        # Before sample 0 the stream is the left-pad silence, so reflect padding gives zeros.
        excerpt = audio[max(lo, 0) : hi]
        if lo < 0:
            excerpt = np.concatenate([np.zeros(-lo, dtype=np.float32), excerpt])
        idx = np.arange(N_FFT)[None, :] + (np.arange(end - first) * HOP)[:, None]
        frames = mx.array(excerpt[idx]) * self._window[None, :]
        power = mx.abs(mx.fft.rfft(frames, n=N_FFT, axis=-1)) ** 2  # [frames, freq]
        mel = power @ self._mel_filters  # [frames, 128]
        log_spec = mx.maximum(mx.log10(mx.maximum(mel, 1e-10)), self._log_mel_min)
        return (log_spec + 4.0) / 4.0

    def _decode(self, max_steps: int | None) -> None:
        """1 decoder step per new audio token; the prompt positions in 1 call."""
        import mlx.core as mx

        st = self._state
        if st["done"] or not st["adapter"]:
            return
        decoder = self._model.decoder
        audio = mx.concatenate(st["adapter"], axis=0) if len(st["adapter"]) > 1 else st["adapter"][0]
        used = 0
        prompt_len = len(self._prompt_ids)

        if st["pos"] < prompt_len:
            n = min(prompt_len - st["pos"], audio.shape[0])
            ids = mx.array(self._prompt_ids[st["pos"] : st["pos"] + n])
            embeds = audio[:n] + decoder.embed_tokens(ids)
            h, st["dec_cache"] = decoder.forward(embeds, start_pos=st["pos"], cache=st["dec_cache"])
            st["pos"] += n
            used = n
            if st["pos"] == prompt_len:
                self._take_token(decoder.logits(h[-1]))
            else:
                mx.eval([t for kv in st["dec_cache"] for t in kv[:2]])

        steps = 0
        while used < audio.shape[0] and not st["done"] and (max_steps is None or steps < max_steps):
            steps += 1
            embed = audio[used] + decoder.embed_token(st["generated"][-1])
            h, st["dec_cache"] = decoder.forward(embed[None, :], start_pos=st["pos"], cache=st["dec_cache"])
            st["pos"] += 1
            used += 1
            self._take_token(decoder.logits(h[0]))

        st["adapter"] = [audio[used:]] if used < audio.shape[0] and not st["done"] else []

    def _take_token(self, logits: Any) -> None:
        import mlx.core as mx

        token = int(mx.argmax(logits).item())
        self._state["generated"].append(token)
        if token == self._eos_id:
            self._state["done"] = True
