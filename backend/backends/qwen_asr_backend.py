"""
Qwen3-ASR STT backend (MLX, Apple Silicon).

Uses the Qwen3-ASR implementation that ships with the pinned mlx-audio, so no
extra dependency is needed. Qwen3-ASR takes language *names* ("English"), not
ISO codes — ``to_qwen_language`` converts the codes the rest of the app uses.
"""

import asyncio
import logging
from collections.abc import Iterable

from . import LANGUAGE_CODE_TO_NAME
from .base import is_model_cached, model_load_progress

logger = logging.getLogger(__name__)

# 8-bit text decoder, audio encoder not quantized. In the phase 0 spike these
# had about the same WER as the bf16 repos, and were 20-30 % faster.
QWEN_ASR_HF_REPOS = {
    "0.6B": "mlx-community/Qwen3-ASR-0.6B-8bit",
    "1.7B": "mlx-community/Qwen3-ASR-1.7B-8bit",
}

# Used when no language is known. Auto-detection is not reliable (in the
# phase 0 spike German audio was detected as English), so always pass a name.
DEFAULT_QWEN_LANGUAGE = "English"

# Codes Qwen3-ASR supports beyond the shared LANGUAGE_CODE_TO_NAME table.
_EXTRA_LANGUAGE_NAMES = {
    "yue": "cantonese",
    "ar": "arabic",
    "id": "indonesian",
    "th": "thai",
    "vi": "vietnamese",
    "tr": "turkish",
    "hi": "hindi",
    "ms": "malay",
    "nl": "dutch",
    "sv": "swedish",
    "da": "danish",
    "fi": "finnish",
    "pl": "polish",
    "cs": "czech",
    "fil": "filipino",
    "tl": "filipino",
    "fa": "persian",
    "el": "greek",
    "ro": "romanian",
    "hu": "hungarian",
    "mk": "macedonian",
}

# Language names from the Qwen3-ASR model config (support_languages).
QWEN_ASR_LANGUAGES = [
    "Chinese", "English", "Cantonese", "Arabic", "German", "French", "Spanish",
    "Portuguese", "Indonesian", "Italian", "Korean", "Russian", "Thai",
    "Vietnamese", "Japanese", "Turkish", "Hindi", "Malay", "Dutch", "Swedish",
    "Danish", "Finnish", "Polish", "Czech", "Filipino", "Persian", "Greek",
    "Romanian", "Hungarian", "Macedonian",
]

# ISO codes for the model config ``languages`` list.
QWEN_ASR_LANGUAGE_CODES = list(LANGUAGE_CODE_TO_NAME.keys()) + [
    code for code in _EXTRA_LANGUAGE_NAMES if code != "tl"
]


def to_qwen_language(
    code: str | None,
    supported: Iterable[str] | None = None,
    default: str = DEFAULT_QWEN_LANGUAGE,
) -> str:
    """Convert an ISO language code to the name Qwen3-ASR expects.

    Always returns a name: ``None`` and ``"auto"`` give ``default``, and an
    unknown or unsupported code gives ``default`` with a warning.
    """
    if not code or code.lower() == "auto":
        return default

    supported_by_lower = {name.lower(): name for name in (supported or QWEN_ASR_LANGUAGES)}
    key = code.lower()
    name = LANGUAGE_CODE_TO_NAME.get(key) or _EXTRA_LANGUAGE_NAMES.get(key) or key
    if name in supported_by_lower:
        return supported_by_lower[name]

    logger.warning("Qwen3-ASR does not support language %r; using %s", code, default)
    return default


class MLXQwenASRBackend:
    """MLX-based STT backend using mlx-audio Qwen3-ASR."""

    def __init__(self, model_size: str = "0.6B"):
        self.model = None
        self.model_size = model_size

    def is_loaded(self) -> bool:
        return self.model is not None

    def _get_model_path(self, model_size: str) -> str:
        if model_size not in QWEN_ASR_HF_REPOS:
            raise ValueError(
                f"Unknown Qwen3-ASR size '{model_size}'. Must be one of: {', '.join(QWEN_ASR_HF_REPOS)}"
            )
        return QWEN_ASR_HF_REPOS[model_size]

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

        with model_load_progress(f"qwen-asr-{model_size}", is_cached):
            from mlx_audio.stt import load

            # Drop the old size first so 2 Qwen3-ASR models are never in memory.
            self.unload_model()
            logger.info("Loading MLX Qwen3-ASR model %s...", model_size)
            self.model = load(repo)

        self.model_size = model_size
        logger.info("MLX Qwen3-ASR model %s loaded successfully", model_size)

    def unload_model(self):
        if self.model is not None:
            del self.model
            self.model = None
            logger.info("MLX Qwen3-ASR model unloaded")

    async def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        model_size: str | None = None,
    ) -> str:
        await self.load_model_async(model_size)

        def _transcribe_sync():
            supported = getattr(self.model.config, "support_languages", None)
            result = self.model.generate(
                str(audio_path), language=to_qwen_language(language, supported)
            )
            return result.text.strip()

        return await asyncio.to_thread(_transcribe_sync)
