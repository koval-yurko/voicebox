"""
Unit tests for the multi-engine STT registry. No model download needed.
"""

import logging

import pytest
from pydantic import ValidationError

from backend import backends
from backend.backends.qwen_asr_backend import MLXQwenASRBackend, to_qwen_language
from backend.models import (
    CaptureRetranscribeRequest,
    CaptureSettingsResponse,
    CaptureSettingsUpdate,
    TranscriptionRequest,
)
from backend.services import transcribe


@pytest.fixture(autouse=True)
def mlx_registry(monkeypatch):
    """Qwen3-ASR is MLX only — force the MLX registry and start clean."""
    monkeypatch.setattr(backends, "get_backend_type", lambda: "mlx")
    backends.reset_backends()
    yield
    backends.reset_backends()


class FakeSTTBackend:
    def __init__(self, model_size: str = "base"):
        self.model = None
        self.model_size = model_size
        self.calls: list[tuple[str, str | None, str | None]] = []

    def is_loaded(self) -> bool:
        return self.model is not None

    def _is_model_cached(self, model_size: str) -> bool:
        return True

    async def load_model(self, model_size: str | None = None) -> None:
        self.model = object()
        self.model_size = model_size or self.model_size

    async def transcribe(self, audio_path, language=None, model_size=None) -> str:
        await self.load_model(model_size)
        self.calls.append((audio_path, language, model_size))
        return f"text from {model_size}"

    def unload_model(self) -> None:
        self.model = None


def _install_fakes() -> tuple[FakeSTTBackend, FakeSTTBackend]:
    whisper, qwen = FakeSTTBackend("base"), FakeSTTBackend("0.6B")
    backends._stt_backends["whisper"] = whisper
    backends._stt_backends["qwen_asr"] = qwen
    return whisper, qwen


# ─── resolve_stt_model ─────────────────────────────────────────────────────


@pytest.mark.parametrize("size", ["base", "small", "medium", "large", "turbo"])
def test_resolve_old_whisper_sizes(size):
    cfg = backends.resolve_stt_model(size)
    assert cfg.model_name == f"whisper-{size}"
    assert cfg.engine == "whisper"
    assert cfg.model_size == size


def test_resolve_full_names():
    assert backends.resolve_stt_model("whisper-turbo").model_size == "turbo"
    cfg = backends.resolve_stt_model("qwen-asr-0.6B")
    assert cfg.engine == "qwen_asr"
    assert cfg.model_size == "0.6B"
    assert backends.resolve_stt_model("qwen-asr-1.7B").hf_repo_id == "mlx-community/Qwen3-ASR-1.7B-8bit"


def test_resolve_none_uses_default():
    assert backends.resolve_stt_model(None).model_name == backends.DEFAULT_STT_MODEL
    assert backends.resolve_stt_model(None, default="small").model_name == "whisper-small"
    assert backends.resolve_stt_model("", default="qwen-asr-0.6B").engine == "qwen_asr"


def test_resolve_unknown_raises():
    with pytest.raises(ValueError, match="Unknown STT model"):
        backends.resolve_stt_model("bogus")


def test_qwen_asr_configs_in_registry():
    names = {c.model_name for c in backends.get_stt_model_configs()}
    assert {"qwen-asr-0.6B", "qwen-asr-1.7B"} <= names
    all_names = {c.model_name for c in backends.get_all_model_configs()}
    assert {"qwen-asr-0.6B", "qwen-asr-1.7B"} <= all_names
    assert backends.get_model_config("qwen-asr-0.6B").engine == "qwen_asr"


def test_qwen_asr_not_offered_without_mlx(monkeypatch):
    monkeypatch.setattr(backends, "get_backend_type", lambda: "pytorch")
    names = {c.model_name for c in backends.get_stt_model_configs()}
    assert "qwen-asr-0.6B" not in names
    with pytest.raises(ValueError, match="Unknown STT model"):
        backends.resolve_stt_model("qwen-asr-0.6B")


# ─── to_qwen_language ──────────────────────────────────────────────────────


def test_to_qwen_language_known_codes():
    assert to_qwen_language("en") == "English"
    assert to_qwen_language("zh") == "Chinese"
    assert to_qwen_language("ru") == "Russian"
    assert to_qwen_language("nl") == "Dutch"
    assert to_qwen_language("EN") == "English"


@pytest.mark.parametrize("code", [None, "", "auto"])
def test_to_qwen_language_auto_gives_default(code):
    assert to_qwen_language(code) == "English"
    assert to_qwen_language(code, default="German") == "German"


@pytest.mark.parametrize("code", ["uk", "xx"])
def test_to_qwen_language_unsupported_warns_and_gives_default(code, caplog):
    with caplog.at_level(logging.WARNING):
        assert to_qwen_language(code) == "English"
    assert code in caplog.text


def test_to_qwen_language_uses_model_supported_list():
    assert to_qwen_language("en", supported=["english"]) == "english"
    assert to_qwen_language("de", supported=["English"]) == "English"


def test_default_stt_model_is_whisper_turbo():
    assert backends.DEFAULT_STT_MODEL == "whisper-turbo"
    assert CaptureSettingsResponse().stt_model == "turbo"
    assert backends.resolve_stt_model(CaptureSettingsResponse().stt_model).model_name == "whisper-turbo"


# ─── Backend instances and engine switch ───────────────────────────────────


def test_get_stt_backend_one_instance_per_engine():
    qwen = backends.get_stt_backend("qwen_asr")
    assert isinstance(qwen, MLXQwenASRBackend)
    assert backends.get_stt_backend("qwen_asr") is qwen
    assert backends.get_stt_backend() is backends.get_stt_backend("whisper")
    assert backends.get_stt_backend("whisper") is not qwen


def test_get_stt_backend_unknown_engine():
    with pytest.raises(ValueError, match="Unknown STT engine"):
        backends.get_stt_backend("nope")


async def test_engine_switch_unloads_other_engine():
    whisper, qwen = _install_fakes()

    text = await transcribe.transcribe_with_model(
        backends.resolve_stt_model("turbo"), "/tmp/a.wav", "en"
    )
    assert text == "text from turbo"
    assert whisper.is_loaded()
    assert not qwen.is_loaded()

    text = await transcribe.transcribe_with_model(
        backends.resolve_stt_model("qwen-asr-0.6B"), "/tmp/a.wav", "en"
    )
    assert text == "text from 0.6B"
    assert qwen.is_loaded()
    assert not whisper.is_loaded()


async def test_load_stt_model_unloads_other_engine():
    whisper, qwen = _install_fakes()
    await whisper.load_model("turbo")

    await transcribe.load_stt_model(backends.resolve_stt_model("qwen-asr-1.7B"))
    assert qwen.is_loaded()
    assert qwen.model_size == "1.7B"
    assert not whisper.is_loaded()


async def test_model_status_helpers_per_engine():
    _, qwen = _install_fakes()
    qwen_cfg = backends.resolve_stt_model("qwen-asr-0.6B")
    await qwen.load_model("0.6B")

    assert backends.check_model_loaded(qwen_cfg)
    assert not backends.check_model_loaded(backends.resolve_stt_model("qwen-asr-1.7B"))
    assert not backends.check_model_loaded(backends.resolve_stt_model("turbo"))

    assert backends.unload_model_by_config(qwen_cfg) is True
    assert not qwen.is_loaded()
    assert backends.unload_model_by_config(qwen_cfg) is False


def test_get_stt_for_model_and_readiness():
    whisper, qwen = _install_fakes()
    backend, cfg = transcribe.get_stt_for_model(None, default="qwen-asr-0.6B")
    assert backend is qwen
    assert cfg.model_name == "qwen-asr-0.6B"
    assert transcribe.is_stt_model_ready(backend, cfg)
    assert transcribe.get_whisper_model() is whisper


# ─── Pydantic request models ───────────────────────────────────────────────


@pytest.mark.parametrize("value", ["turbo", "base", "whisper-turbo", "qwen-asr-0.6B", "qwen-asr-1.7B"])
def test_request_models_accept_both_forms(value):
    assert TranscriptionRequest(model=value).model == value
    assert CaptureRetranscribeRequest(model=value).model == value
    assert CaptureSettingsUpdate(stt_model=value).stt_model == value


def test_request_models_accept_none():
    assert TranscriptionRequest().model is None
    assert CaptureSettingsUpdate().stt_model is None


@pytest.mark.parametrize("value", ["bogus", "whisper-tiny", "qwen-asr-9B"])
def test_request_models_reject_unknown(value):
    with pytest.raises(ValidationError):
        TranscriptionRequest(model=value)
    with pytest.raises(ValidationError):
        CaptureRetranscribeRequest(model=value)
    with pytest.raises(ValidationError):
        CaptureSettingsUpdate(stt_model=value)
