"""
Slow test: download, load and transcribe a clip with Qwen3-ASR 0.6B.

Downloads about 1 GB on the first run. Needs Apple Silicon (MLX) and the
macOS ``say`` command for the speech clip. Skipped unless
``VOICEBOX_RUN_SLOW=1``:

    VOICEBOX_RUN_SLOW=1 backend/venv/bin/python -m pytest backend/tests/test_qwen_asr_download.py -v
"""

import os
import shutil
import subprocess

import pytest

from backend import backends
from backend.services import transcribe

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("VOICEBOX_RUN_SLOW") != "1",
        reason="slow model download; set VOICEBOX_RUN_SLOW=1 to run",
    ),
]

CLIP_TEXT = "The quick brown fox jumps over the lazy dog."


@pytest.fixture
def speech_clip(tmp_path):
    if shutil.which("say") is None:
        pytest.skip("macOS `say` is needed to make the speech clip")
    path = tmp_path / "clip.wav"
    subprocess.run(
        ["say", "-o", str(path), "--data-format=LEI16@16000", CLIP_TEXT],
        check=True,
    )
    return path


async def test_qwen_asr_download_and_transcribe(speech_clip):
    if backends.get_backend_type() != "mlx":
        pytest.skip("Qwen3-ASR needs the MLX backend (Apple Silicon)")

    backends.reset_backends()
    config = backends.resolve_stt_model("qwen-asr-0.6B")

    await transcribe.load_stt_model(config)
    backend = backends.get_stt_backend("qwen_asr")
    assert backend.is_loaded()
    assert backends.check_model_loaded(config)

    text = await transcribe.transcribe_with_model(config, str(speech_clip), "en")
    words = text.lower()
    for word in ("quick", "brown", "fox", "lazy", "dog"):
        assert word in words, text

    # No language falls back to English (auto-detect is not used).
    auto_text = await transcribe.transcribe_with_model(config, str(speech_clip), None)
    assert "fox" in auto_text.lower(), auto_text

    assert backends.unload_model_by_config(config)
    assert not backend.is_loaded()
    backends.reset_backends()
