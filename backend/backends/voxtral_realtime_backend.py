"""
Voxtral Mini 4B Realtime STT backend (MLX, Apple Silicon).

Uses the Voxtral Realtime implementation that ships with the pinned mlx-audio.
The model is built for streaming (``voxtral_streaming.py``); this backend is
the batch path (``POST /transcribe``). Voxtral Realtime detects the language
by itself and takes no language prompt.
"""

import asyncio
import logging

from .base import is_model_cached, model_load_progress

logger = logging.getLogger(__name__)

# 4-bit, mlx-audio weight names. No 8-bit repo with mlx-audio names exists
# (2026-10-29): the 8-bit repos use voxmlx or Mistral names and quantize the
# embeddings, which the mlx-audio model reads as a plain matrix.
VOXTRAL_REALTIME_HF_REPOS = {
    "4B": "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
}

# Languages from the model card.
VOXTRAL_REALTIME_LANGUAGE_CODES = ["en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it", "ar", "hi", "nl"]


def materialize_model_arrays(model) -> None:
    """Evaluate the lazy constant arrays of the model on this thread.

    MLX streams are per thread: a lazy array made on the load thread cannot be
    evaluated on the stream worker thread. The decoder RoPE frequencies are
    not parameters, so the loader does not evaluate them.
    """
    import mlx.core as mx

    mx.eval([layer.attention._rope_inv_freq for layer in model.decoder.layers])


class MLXVoxtralRealtimeBackend:
    """MLX-based STT backend using mlx-audio Voxtral Realtime."""

    def __init__(self, model_size: str = "4B"):
        self.model = None
        self.model_size = model_size

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_model_path(self, model_size: str) -> str:
        if model_size not in VOXTRAL_REALTIME_HF_REPOS:
            raise ValueError(
                f"Unknown Voxtral Realtime size '{model_size}'. Must be one of: {', '.join(VOXTRAL_REALTIME_HF_REPOS)}"
            )
        return VOXTRAL_REALTIME_HF_REPOS[model_size]

    def _is_model_cached(self, model_size: str) -> bool:
        return is_model_cached(self._get_model_path(model_size))

    async def load_model_async(self, model_size: str | None = None):
        if model_size is None:
            model_size = self.model_size

        if self.model is not None and self.model_size == model_size:
            return

        await asyncio.to_thread(self._load_model_sync, model_size)

    load_model = load_model_async

    def _load_model_sync(self, model_size: str):
        repo = self._get_model_path(model_size)
        is_cached = self._is_model_cached(model_size)

        with model_load_progress(f"voxtral-realtime-{model_size}", is_cached):
            from mlx_audio.stt import load

            self.unload_model()
            logger.info("Loading MLX Voxtral Realtime model %s...", model_size)
            model = load(repo)
            materialize_model_arrays(model)
            self.model = model

        self.model_size = model_size
        logger.info("MLX Voxtral Realtime model %s loaded successfully", model_size)

    def unload_model(self):
        if self.model is not None:
            del self.model
            self.model = None
            logger.info("MLX Voxtral Realtime model unloaded")

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        model_size: str | None = None,
    ) -> str:
        await self.load_model_async(model_size)

        def _transcribe_sync():
            return self.model.generate(str(audio_path)).text.strip()

        return await asyncio.to_thread(_transcribe_sync)
