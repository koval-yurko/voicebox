"""Real-time STT streaming endpoint.

``WS /stt/ws`` takes PCM16 audio frames and sends text events back. It works
for every registered STT model: models with no streaming adapter go through
``SegmentedBatchStream`` (FINAL text only). Protocol:
``docs/research/stt-streaming-design.md`` section 7.
"""

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..backends import open_stt_stream
from ..backends.stt_streaming import StreamSession, STTStreamOptions
from ..database import get_db
from ..services import settings as settings_service, transcribe

logger = logging.getLogger(__name__)

router = APIRouter()


class STTStreamStart(BaseModel):
    """The ``start`` message of ``WS /stt/ws``."""

    model: str | None = Field(
        default=None, description="STT model name or old Whisper size; empty uses the capture settings default"
    )
    language: str | None = Field(default=None, pattern="^[a-zA-Z-]{2,10}$")
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    vad: bool = True
    interim: bool = True
    interim_interval_ms: int = Field(default=500, ge=100, le=5000)
    max_segment_s: float = Field(default=30.0, ge=1.0, le=120.0)
    delay_ms: int | None = Field(
        default=None,
        description="Voxtral Realtime transcription delay: a multiple of 80 from 80 to 1200, or 2400. "
        "Lower is faster with more errors. Empty uses the server default (240). Other models ignore it.",
    )

    @field_validator("delay_ms")
    @classmethod
    def _check_delay(cls, value: int | None) -> int | None:
        if value is not None and value != 2400 and not (80 <= value <= 1200 and value % 80 == 0):
            raise ValueError("delay_ms must be a multiple of 80 from 80 to 1200, or 2400")
        return value


def _default_stt_model() -> str | None:
    db = next(get_db())
    try:
        return settings_service.get_capture_settings(db).stt_model
    finally:
        db.close()


async def _open_session(msg: dict) -> StreamSession:
    """Validate ``start``, check that the model is on disk, then load it."""
    start = STTStreamStart(**{k: v for k, v in msg.items() if k != "type"})
    backend, config = transcribe.get_stt_for_model(start.model, default=_default_stt_model())
    if not transcribe.is_stt_model_ready(backend, config):
        raise ValueError(
            f"{config.display_name} is not downloaded. Download it first (POST /models/download "
            f'with model_name "{config.model_name}").'
        )
    options = STTStreamOptions(
        model=config.model_name,
        language=start.language,
        sample_rate=start.sample_rate,
        interim_interval_ms=start.interim_interval_ms,
        max_segment_s=start.max_segment_s,
        vad=start.vad,
        interim=start.interim,
        delay_ms=start.delay_ms,
    )
    session = open_stt_stream(options)
    await session.start()
    return session


@router.websocket("/stt/ws")
async def stt_ws(ws: WebSocket):
    """Streaming speech-to-text over one WebSocket.

    Client → server:
      {"type": "start", "model": "qwen-asr-0.6B", "language": "en", "sample_rate": 16000,
       "vad": true, "interim": true}
      binary frames: PCM16 little-endian mono at sample_rate
      {"type": "end_segment"}       client endpoint (when vad is false)
      {"type": "cancel_segment"}    drop the open segment, no final (barge-in, false start)
      {"type": "end"}               finish the open segment, send its final, then close

    Server → client:
      {"type": "ready", "model", "sample_rate", "vad", "capabilities": {...}}
      {"type": "speech_start", "segment_id", "start_s"}
      {"type": "interim", "segment_id", "text", "stable_text"}
      {"type": "final", "segment_id", "text", "language", "start_s", "end_s"}
      {"type": "speech_end", "segment_id", "end_s"}
      {"type": "error", "message"} | {"type": "end"}
    """
    await ws.accept()
    session: StreamSession | None = None
    pump: asyncio.Task | None = None

    async def _send_json(payload: dict):
        await ws.send_text(json.dumps(payload))

    async def _pump_events(s: StreamSession):
        async for event in s.events():
            await _send_json(event.to_message())

    async def _close_session(finalize: bool):
        nonlocal session, pump
        if session is not None:
            await session.aclose(finalize=finalize)
        if pump is not None:
            if finalize:
                await pump
            else:
                pump.cancel()
                with contextlib.suppress(BaseException):
                    await pump
        session, pump = None, None

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                if session is None:
                    await _send_json({"type": "error", "message": "send a start message first"})
                else:
                    await session.push_pcm16(data)
                continue

            try:
                msg = json.loads(message.get("text") or "")
            except json.JSONDecodeError:
                await _send_json({"type": "error", "message": "invalid JSON"})
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None

            if kind == "start":
                await _close_session(finalize=False)
                try:
                    session = await _open_session(msg)
                except ValidationError as e:
                    await _send_json({"type": "error", "message": f"invalid start message: {e.errors()}"})
                    continue
                except Exception as e:
                    await _send_json({"type": "error", "message": str(e)})
                    continue
                pump = asyncio.create_task(_pump_events(session))
                await _send_json(
                    {
                        "type": "ready",
                        "model": session.adapter.config.model_name,
                        "sample_rate": session.options.sample_rate,
                        "vad": session.options.vad,
                        "capabilities": session.capabilities.to_dict(),
                    }
                )
            elif session is None:
                await _send_json({"type": "error", "message": "send a start message first"})
            elif kind == "end_segment":
                await session.end_segment()
            elif kind == "cancel_segment":
                await session.cancel_segment()
            elif kind == "end":
                await _close_session(finalize=True)
                await _send_json({"type": "end"})
                break
            else:
                await _send_json({"type": "error", "message": f"unknown message type: {kind}"})
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /stt/ws")
    finally:
        # Client gone (or error): drop open work instead of decoding for nobody.
        await _close_session(finalize=False)
        with contextlib.suppress(Exception):
            await ws.close()
