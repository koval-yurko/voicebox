"""Transcription endpoints."""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import settings as settings_service
from ..services import transcribe
from ..services.task_queue import create_background_task
from ..utils.tasks import get_task_manager

router = APIRouter()

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

# Same set profiles.py accepts for voice samples. librosa picks its decoder from the
# file extension, so the temp file has to keep the uploaded one.
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".webm", ".opus"}


@router.post("/transcribe", response_model=models.TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Transcribe audio file to text.

    ``model`` is an STT model name (``whisper-turbo``, ``qwen-asr-0.6B``) or an
    old Whisper size (``turbo``). Empty uses the capture settings default.
    """
    uploaded_ext = Path(file.filename or "").suffix.lower()
    file_suffix = uploaded_ext if uploaded_ext in ALLOWED_AUDIO_EXTS else ".wav"

    with tempfile.NamedTemporaryFile(suffix=file_suffix, delete=False) as tmp:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            tmp.write(chunk)
        tmp_path = tmp.name

    stt_path = tmp_path
    try:
        from ..utils.audio import load_audio, save_audio

        saved = settings_service.get_capture_settings(db)
        try:
            stt_model, config = transcribe.get_stt_for_model(model, default=saved.stt_model)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        audio, sr = await asyncio.to_thread(load_audio, tmp_path)
        duration = len(audio) / sr

        # The STT backend (mlx_audio.stt -> miniaudio) only decodes
        # WAV/FLAC/MP3/Vorbis, so browser recordings uploaded as WebM/Opus
        # fail with "unsupported file format" (issue: web-mode dictation).
        # librosa already decoded the file above (it falls back to
        # audioread/ffmpeg for exotic containers), so re-encode that PCM to a
        # temp WAV and hand *that* to the STT engine. WAV inputs pass through
        # unchanged.
        if file_suffix != ".wav":
            stt_path = f"{tmp_path}.stt.wav"
            await asyncio.to_thread(save_audio, audio, stt_path, sr)

        if not transcribe.is_stt_model_ready(stt_model, config):
            progress_model_name = config.model_name
            task_manager = get_task_manager()

            async def download_stt_background():
                try:
                    await transcribe.load_stt_model(config)
                    task_manager.complete_download(progress_model_name)
                except Exception as e:
                    task_manager.error_download(progress_model_name, str(e))

            task_manager.start_download(progress_model_name)
            create_background_task(download_stt_background())

            raise HTTPException(
                status_code=202,
                detail={
                    "message": f"{config.display_name} model is being downloaded. Please wait and try again.",
                    "model_name": progress_model_name,
                    "downloading": True,
                },
            )

        text = await transcribe.transcribe_with_model(config, stt_path, language)

        return models.TranscriptionResponse(
            text=text,
            duration=duration,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        if stt_path != tmp_path:
            Path(stt_path).unlink(missing_ok=True)
