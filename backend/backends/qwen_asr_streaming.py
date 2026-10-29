"""
Qwen3-ASR rolling streaming adapter (design doc section 5.3).

The Qwen3-ASR audio encoder is block-local: attention works on 8 s windows
(800 mel frames, 104 audio tokens), and the conv stem works on 1 s chunks. So
the encoder output of a closed 8 s window never changes when more audio
arrives. Each interim step therefore:

1. computes the mel and the encoder output of the open window only;
2. trims the decoder KV cache back to the prefix (system prompt +
   ``<audio_start>`` + the audio tokens of the closed windows);
3. prefills the open-window audio, the rest of the prompt and the committed
   text, then decodes at most 24 new tokens.

FINAL text comes from the batch path (``transcribe()`` on the segment audio),
so it always equals ``POST /transcribe``. In phase 0 a final decode on top of
the prefix cache gave a different token on 5-8 % of segments (bf16 ties).

This uses internals of mlx-audio 0.4.1 (``Qwen3ASRModel.audio_tower``,
``_build_prompt``, ``make_cache``). Keep mlx-audio pinned; the model tests in
``backend/tests/test_qwen_asr_streaming.py`` compare against batch mode.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from .stt_streaming import STREAM_SAMPLE_RATE, RollingRedecodeStream, SegmentedBatchStream, run_blocking

if TYPE_CHECKING:
    from . import ModelConfig, STTBackend

logger = logging.getLogger(__name__)

HOP = 160  # mel hop (10 ms at 16 kHz)
N_FFT = 400
CHUNK_FRAMES = 100  # conv stem chunk: 1 s
WINDOW_FRAMES = 800  # encoder attention window: 8 s
WINDOW_SAMPLES = WINDOW_FRAMES * HOP
# Frames 0 and 1 of an STFT excerpt use reflect padding. Start the excerpt 2
# hops early and drop them, so the window frames equal batch mode.
EDGE_SAMPLES = 2 * HOP
# A frame centred at sample c uses samples up to c + N_FFT // 2. The last
# frame of a window is final when this many samples after the window end exist.
CLOSE_SAMPLES = N_FFT // 2 - HOP
EOS_TOKEN_IDS = (151645, 151643)  # <|im_end|>, <|endoftext|> (as in mlx-audio stream_generate)
MEL_FLOOR_DB = 8.0  # WhisperFeatureExtractor clamps the log-mel to max - 8


class QwenASRRollingStream(RollingRedecodeStream):
    """Interim text for Qwen3-ASR from a rolling re-decode with cached closed windows."""

    def __init__(self, backend: STTBackend, config: ModelConfig, language: str | None = None):
        super().__init__(config.model_name)
        self.backend = backend
        self.config = config
        self.language = language
        self._batch = SegmentedBatchStream(backend, config, language)
        self._model: Any = None  # Qwen3ASRModel of the loaded backend model
        self._positions: Any = None
        self._loaded: Any = None  # backend.model that _model belongs to
        self._clear_audio_state()

    # -- loading --

    async def prepare(self) -> None:
        await self._batch.prepare()
        await run_blocking(self.model_name, self._warm_up)

    def _warm_up(self) -> None:
        """1 step on silence, so the first real step has no first-run cost."""
        self._interim_step(np.zeros(STREAM_SAMPLE_RATE, dtype=np.float32), -1)
        self.step_times_ms = []  # not a real segment: no step-time log
        self._clear(-1)

    async def interim(self, audio: np.ndarray, segment_id: int) -> tuple[str, str] | None:
        from . import unload_other_stt_engines

        # Another request can unload or switch the model between steps.
        unload_other_stt_engines(self.config.engine)
        await self.backend.load_model(self.config.model_size)
        return await super().interim(audio, segment_id)

    async def finalize(self, audio: np.ndarray, segment_id: int) -> str:
        return await self._batch.finalize(audio, segment_id)

    def _bind_model(self) -> None:
        """Use the backend's current model. Called on the worker thread."""
        loaded = self.backend.model
        if loaded is None:
            raise RuntimeError(f"{self.config.model_name} is not loaded")
        if loaded is self._loaded:
            return
        from .qwen_asr_backend import to_qwen_language

        model = getattr(loaded, "_model", loaded)  # mlx-audio wraps Qwen3ASRModel in Model
        language = to_qwen_language(self.language, model.config.support_languages)
        ids = np.array(model._build_prompt(1, language)).reshape(-1).tolist()
        audio_id = model.config.audio_token_id
        split = ids.index(audio_id)
        self._audio_token_id = audio_id
        self._prefix_ids = ids[:split]  # <|im_start|>system ... <|audio_start|>
        self._suffix_ids = ids[split + 1 :]  # <|audio_end|> ... language X<asr_text>
        self._tokenizer = model._tokenizer
        self._feature_extractor = model._feature_extractor
        self._model = model
        self._loaded = loaded
        self._clear_audio_state()

    # -- RollingRedecodeStream hooks --

    def begin_segment(self) -> None:
        self._bind_model()
        self._clear_audio_state()
        self._cache = self._model.make_cache()
        self._prefill(self._prefix_ids, None)
        self._prefix_len = self._cache[0].offset

    def free_segment(self) -> None:
        self._clear_audio_state()

    def encode_incremental(self, audio: np.ndarray) -> None:
        if self.backend.model is not self._loaded:
            # The model was reloaded after the segment started: rebuild the state.
            self.begin_segment()

        closed_before = len(self._closed_mel)
        while len(audio) >= (len(self._closed_mel) + 1) * WINDOW_SAMPLES + CLOSE_SAMPLES:
            self._closed_mel.append(self._log_mel(audio, len(self._closed_mel)))

        open_mel = self._log_mel(audio, len(self._closed_mel))
        mel_max = self._final_max(open_mel)

        if mel_max != self._closed_max and self._closed_feats:
            # The segment maximum changed the clamp of the closed windows:
            # encode them again and rebuild the prefix cache (rare).
            self._closed_feats = []
            for c in self._cache:
                c.trim(c.offset - len(self._prefix_ids))
            self._prefix_len = self._cache[0].offset
        self._closed_max = mel_max
        for mel in self._closed_mel[len(self._closed_feats) :]:
            feats = self._encode_window(self._normalize(mel, mel_max))
            self._trim_to_prefix()
            self._prefill([self._audio_token_id] * feats.shape[0], feats)
            self._prefix_len = self._cache[0].offset
            self._closed_feats.append(feats)
        if len(self._closed_mel) > closed_before:
            logger.debug("STT rolling %s: %d closed windows", self.model_name, len(self._closed_mel))

        self._open_feats = self._encode_window(self._normalize(open_mel, mel_max)) if open_mel.shape[1] else None

    def decode(self, prefix_ids: list[int], max_new: int) -> list[int]:
        import mlx.core as mx

        self._trim_to_prefix()
        text_ids = self._suffix_ids + prefix_ids
        embeds = self._model.model.embed_tokens(mx.array(text_ids))
        ids = text_ids
        if self._open_feats is not None:
            embeds = mx.concatenate([self._open_feats.astype(embeds.dtype), embeds], axis=0)
            ids = [self._audio_token_id] * self._open_feats.shape[0] + text_ids

        # Greedy decode like mlx_lm generate_step: all prompt tokens except the
        # last in 1 call, then the last token alone. generate_step itself is
        # not used: its module-level MLX stream belongs to the thread that
        # imported mlx_lm.generate, and MLX streams are per thread.
        model, cache = self._model, self._cache
        if len(ids) > 1:
            model(mx.array(ids[:-1])[None], input_embeddings=embeds[:-1][None], cache=cache)
        logits = model(mx.array(ids[-1:])[None], input_embeddings=embeds[-1:][None], cache=cache)
        out: list[int] = []
        while True:
            token = int(mx.argmax(logits[0, -1]).item())
            if token in EOS_TOKEN_IDS:
                break
            out.append(token)
            if len(out) >= max_new:
                break
            logits = model(mx.array([[token]]), cache=cache)
        return out

    def encode_text(self, text: str) -> list[int]:
        return self._tokenizer.encode(text, add_special_tokens=False)

    def decode_text(self, ids: list[int]) -> str:
        return self._tokenizer.decode(ids, skip_special_tokens=True).strip()

    # -- audio state --

    def _clear_audio_state(self) -> None:
        self._cache: list[Any] | None = None
        self._prefix_len = 0
        self._closed_mel: list[np.ndarray] = []  # raw log-mel (128, 800) per closed window
        self._closed_feats: list[Any] = []  # encoder output (104, d) per closed window
        self._closed_max: float | None = None  # clamp maximum used for _closed_feats
        self._open_feats: Any = None

    def _trim_to_prefix(self) -> None:
        for c in self._cache:
            c.trim(c.offset - self._prefix_len)

    def _prefill(self, ids: list[int], embeds: Any) -> None:
        import mlx.core as mx

        model_input = mx.array(ids)[None]
        if embeds is None:
            self._model(model_input, cache=self._cache)
        else:
            self._model(model_input, input_embeddings=embeds[None], cache=self._cache)
        mx.eval([c.state for c in self._cache])

    # -- mel --

    def _log_mel(self, audio: np.ndarray, window: int) -> np.ndarray:
        """Raw log10 mel (before the clamp) of a window, equal to batch-mode frames.

        A closed window gives 800 frames. The open window gives the frames that
        batch mode would give for the audio received so far.
        """
        start = window * WINDOW_SAMPLES
        excerpt_start = max(0, start - EDGE_SAMPLES)
        drop = (start - excerpt_start) // HOP
        end = min(len(audio), start + WINDOW_SAMPLES + CLOSE_SAMPLES)
        excerpt = audio[excerpt_start:end]
        n_mels = self._feature_extractor.feature_size
        if len(excerpt) // HOP <= drop or len(excerpt) <= N_FFT // 2:
            return np.zeros((n_mels, 0), dtype=np.float32)
        return _raw_log_mel(self._feature_extractor, excerpt)[:, drop : drop + WINDOW_FRAMES]

    def _final_max(self, open_mel: np.ndarray) -> float:
        """Clamp maximum from final frames only (closed windows + open frames with full audio).

        The last open frames still change when audio arrives, so they do not
        count, except when they are all there is.
        """
        values = [float(m.max()) for m in self._closed_mel]
        stable = open_mel[:, : max(0, open_mel.shape[1] - 2)]
        if stable.shape[1]:
            values.append(float(stable.max()))
        if not values and open_mel.shape[1]:
            values.append(float(open_mel.max()))
        return max(values) if values else 0.0

    @staticmethod
    def _normalize(mel: np.ndarray, mel_max: float) -> np.ndarray:
        return (np.maximum(mel, mel_max - MEL_FLOOR_DB) + 4.0) / 4.0

    # -- encoder --

    def _position_table(self, length: int, channels: int) -> Any:
        """Positional embedding of a conv chunk, made on this thread.

        The table in the model is a lazy array of the thread that loaded the
        model; MLX cannot evaluate it on another thread.
        """
        import mlx.core as mx

        if self._positions is None or self._positions.shape != (length, channels):
            self._positions = mx.array(_sinusoid_table(length, channels))
        return self._positions

    def _encode_window(self, mel: np.ndarray) -> Any:
        """Encoder output for 1 window (≤ 800 frames), as batch mode computes it.

        Same steps as ``AudioEncoder.__call__`` for 1 attention block: 1 s
        chunks through the conv stem (the last chunk padded to 100 frames),
        positional embedding per chunk, the valid tokens, then the encoder
        layers with no mask.
        """
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_audio.stt.models.qwen3_asr.qwen3_asr import _get_feat_extract_output_lengths

        tower = self._model.audio_tower
        n_mels, frames = mel.shape
        n_chunks = -(-frames // CHUNK_FRAMES)
        padded = np.zeros((n_mels, n_chunks * CHUNK_FRAMES), dtype=np.float32)
        padded[:, :frames] = mel
        chunks = padded.reshape(n_mels, n_chunks, CHUNK_FRAMES).transpose(1, 0, 2)

        x = mx.array(chunks)[..., None]
        x = nn.gelu(tower.conv2d1(x))
        x = nn.gelu(tower.conv2d2(x))
        x = nn.gelu(tower.conv2d3(x))
        b, f, t, c = x.shape
        x = x.transpose(0, 2, 3, 1).reshape(b, t, c * f)
        x = tower.conv_out(x)
        x = x + self._position_table(t, x.shape[-1])[None, :, :]

        last = frames - (n_chunks - 1) * CHUNK_FRAMES
        last_tokens = int(_get_feat_extract_output_lengths(mx.array([last])).item())
        hidden = x[:-1].reshape(-1, x.shape[-1]) if n_chunks > 1 else x[:0].reshape(0, x.shape[-1])
        hidden = mx.concatenate([hidden, x[-1, :last_tokens]], axis=0)[None]

        for layer in tower.layers:
            hidden = layer(hidden, mask=None)
        hidden = tower.ln_post(hidden[0])
        hidden = nn.gelu(tower.proj1(hidden))
        hidden = tower.proj2(hidden)
        mx.eval(hidden)
        return hidden


def _sinusoid_table(length: int, channels: int, max_timescale: float = 10000.0) -> np.ndarray:
    """Same table as mlx-audio ``SinusoidalPositionEmbedding``."""
    increment = np.log(max_timescale) / (channels // 2 - 1)
    inv_timescales = np.exp(-increment * np.arange(channels // 2, dtype=np.float32))
    scaled = np.arange(length, dtype=np.float32)[:, None] * inv_timescales[None, :]
    return np.concatenate([np.sin(scaled), np.cos(scaled)], axis=1).astype(np.float32)


def _raw_log_mel(feature_extractor: Any, samples: np.ndarray) -> np.ndarray:
    """log10 mel of ``samples`` with the WhisperFeatureExtractor steps, without the clamp.

    Uses the same STFT as the feature extractor (torch when it is installed,
    else numpy), and drops the last frame as it does.
    """
    from transformers.utils import is_torch_available

    fe = feature_extractor
    if is_torch_available():
        import torch

        waveform = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32))
        stft = torch.stft(waveform, fe.n_fft, fe.hop_length, window=torch.hann_window(fe.n_fft), return_complex=True)
        magnitudes = stft[..., :-1].abs() ** 2
        mel = torch.from_numpy(fe.mel_filters).to(torch.float32).T @ magnitudes
        return torch.clamp(mel, min=1e-10).log10().numpy()

    from transformers.audio_utils import spectrogram, window_function

    log_spec = spectrogram(
        samples.astype(np.float32),
        window_function(fe.n_fft, "hann"),
        frame_length=fe.n_fft,
        hop_length=fe.hop_length,
        power=2.0,
        dither=fe.dither,
        mel_filters=fe.mel_filters,
        log_mel="log10",
    )
    return log_spec[:, :-1].astype(np.float32)
