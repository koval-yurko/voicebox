"""
STT (Speech-to-Text) module - delegates to backend abstraction layer.
"""

from ..backends import (
    ModelConfig,
    STTBackend,
    get_stt_backend,
    resolve_stt_model,
    unload_all_stt_engines,
    unload_other_stt_engines,
)


def get_whisper_model() -> STTBackend:
    """
    Get the Whisper STT backend instance (MLX or PyTorch based on platform).

    Kept for old callers; new code uses ``get_stt_for_model``.
    """
    return get_stt_backend("whisper")


def unload_whisper_model():
    """Unload all STT models to free memory (name kept for old callers)."""
    unload_all_stt_engines()


def get_stt_for_model(
    name: str | None, default: str | None = None
) -> tuple[STTBackend, ModelConfig]:
    """Resolve an STT model name (or old Whisper size) to its backend and config.

    Raises:
        ValueError: the name matches no STT model.
    """
    config = resolve_stt_model(name, default)
    return get_stt_backend(config.engine), config


def is_stt_model_ready(backend: STTBackend, config: ModelConfig) -> bool:
    """Whether the model is loaded or on disk (so no download is needed)."""
    if backend.is_loaded() and backend.model_size == config.model_size:
        return True
    return backend._is_model_cached(config.model_size)


async def load_stt_model(config: ModelConfig) -> None:
    """Load (and download if needed) an STT model, unloading other engines first."""
    unload_other_stt_engines(config.engine)
    await get_stt_backend(config.engine).load_model(config.model_size)


async def transcribe_with_model(
    config: ModelConfig, audio_path: str, language: str | None = None
) -> str:
    """Transcribe with the given STT model. Only 1 STT engine stays loaded."""
    unload_other_stt_engines(config.engine)
    backend = get_stt_backend(config.engine)
    return await backend.transcribe(audio_path, language, config.model_size)
