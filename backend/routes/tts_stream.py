"""Real-time TTS streaming endpoints.

Unlike ``POST /generate/stream`` (which renders the whole clip and then sends
one WAV file), these routes send audio while the model is still decoding:

- ``POST /tts/stream``  — chunked HTTP body of raw PCM16 (or a streaming WAV).
- ``WS   /tts/ws``      — incremental text in, binary PCM16 frames out, with
                          flush / cancel for voice-agent barge-in.
- ``POST /tts/warmup``  — load the model and prime the voice prompt.

Engines with a ``generate_stream`` method (MLX Qwen) stream token-level
chunks. Other engines fall back to one chunk per sentence.
"""

import asyncio
import json
import logging
import re
import struct
import time
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Optional, Tuple

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..database import get_db
from ..services import profiles
from ..services.task_queue import inference_lock
from ..utils.chunked_tts import split_text_into_chunks

logger = logging.getLogger(__name__)

router = APIRouter()

LANGUAGE_PATTERN = "^(zh|en|ja|ko|de|fr|ru|pt|es|it|he|ar|da|el|fi|hi|ms|nl|no|pl|sv|sw|tr)$"
ENGINE_PATTERN = "^(qwen|qwen_custom_voice|luxtts|chatterbox|chatterbox_turbo|tada|kokoro)$"


class TTSStreamOptions(BaseModel):
    """Voice and pacing options shared by the HTTP and WebSocket routes."""

    profile_id: str
    language: str = Field(default="en", pattern=LANGUAGE_PATTERN)
    engine: Optional[str] = Field(default=None, pattern=ENGINE_PATTERN)
    model_size: str = Field(default="1.7B", pattern="^(1\\.7B|0\\.6B|1B|3B)$")
    seed: Optional[int] = Field(None, ge=0)
    instruct: Optional[str] = Field(None, max_length=500)
    interval_ms: int = Field(
        default=320, ge=80, le=4000, description="Audio length per streamed chunk (token-streaming engines)"
    )
    max_chunk_chars: int = Field(
        default=400, ge=50, le=5000, description="Max characters per text segment sent to the model"
    )


class TTSStreamRequest(TTSStreamOptions):
    text: str = Field(..., min_length=1, max_length=50000)
    format: Literal["pcm", "wav"] = "pcm"


@dataclass
class _Voice:
    engine: str
    backend: object
    voice_prompt: dict
    opts: TTSStreamOptions
    trim_fn: object = None


async def _prepare_voice(opts: TTSStreamOptions, db: Session) -> _Voice:
    """Resolve profile + engine, load the model and build the voice prompt."""
    from ..backends import ensure_model_cached_or_raise, engine_needs_trim, get_tts_backend_for_engine, load_engine_model

    profile = await profiles.get_profile(opts.profile_id, db)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    engine = (
        opts.engine
        or getattr(profile, "default_engine", None)
        or getattr(profile, "preset_engine", None)
        or "qwen"
    )
    try:
        profiles.validate_profile_engine(profile, engine)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await ensure_model_cached_or_raise(engine, opts.model_size)
    await load_engine_model(engine, opts.model_size)
    backend = get_tts_backend_for_engine(engine)
    voice_prompt = await profiles.create_voice_prompt_for_profile(opts.profile_id, db, engine=engine)

    trim_fn = None
    if engine_needs_trim(engine):
        from ..utils.audio import trim_tts_output

        trim_fn = trim_tts_output

    return _Voice(engine=engine, backend=backend, voice_prompt=voice_prompt, opts=opts, trim_fn=trim_fn)


async def _synthesize(voice: _Voice, text: str) -> AsyncIterator[Tuple[np.ndarray, int]]:
    """Yield (float32 audio, sample_rate) chunks for ``text`` as soon as they exist."""
    opts = voice.opts
    for segment in split_text_into_chunks(text, opts.max_chunk_chars):
        if hasattr(voice.backend, "generate_stream"):
            stream = voice.backend.generate_stream(
                segment,
                voice.voice_prompt,
                language=opts.language,
                seed=opts.seed,
                instruct=opts.instruct,
                interval_s=opts.interval_ms / 1000.0,
            )
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()
        else:
            audio, sample_rate = await voice.backend.generate(
                segment,
                voice.voice_prompt,
                opts.language,
                opts.seed,
                opts.instruct,
            )
            if voice.trim_fn is not None:
                audio = voice.trim_fn(audio, sample_rate)
            yield np.asarray(audio, dtype=np.float32), sample_rate


def _to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def _streaming_wav_header(sample_rate: int, channels: int = 1) -> bytes:
    """WAV header with unknown length (0xFFFFFFFF), as used by streaming players."""
    byte_rate = sample_rate * channels * 2
    return (
        b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, channels * 2, 16)
        + b"data" + struct.pack("<I", 0xFFFFFFFF)
    )


async def _acquire_lock_unless_gone(request: Request) -> bool:
    """Wait for ``inference_lock``, unless the client disconnects first.

    Returns True with the lock held, False (lock not held) when the client is
    gone. ``request.is_disconnected()`` cannot be used: it polls with a zero
    timeout, and through the app's ``BaseHTTPMiddleware`` layers the disconnect
    message never arrives that fast, so it always says "connected". Waiting on
    ``receive()`` next to the lock sees the disconnect when it happens.
    """
    gone = asyncio.ensure_future(request.receive())
    acquire = asyncio.ensure_future(inference_lock.acquire())
    try:
        await asyncio.wait({gone, acquire}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        gone.cancel()
        acquire.cancel()
        raise
    disconnected = gone.done() and not gone.cancelled() and gone.result().get("type") == "http.disconnect"
    if not disconnected:
        gone.cancel()
        if acquire.done():
            return True
        # receive() returned something else (it should not, the body is read): just wait for the lock.
        await acquire
        return True
    acquire.cancel()
    try:
        await acquire
    except asyncio.CancelledError:
        return False
    # The lock was granted in the same moment the client left: give it back.
    inference_lock.release()
    return False


@router.post("/tts/stream")
async def tts_stream(data: TTSStreamRequest, request: Request, db: Session = Depends(get_db)):
    """Stream speech as raw PCM16 (``audio/pcm``) or a streaming WAV while it is generated.

    Response headers carry the format: ``X-Sample-Rate``, ``X-Channels``,
    ``X-Sample-Format: s16le``. The first chunk is produced before the
    response starts, so errors still return a normal HTTP status.

    A client that left while this request waited for the lock is not served:
    a voice agent cancels the queued sentences of a reply the user talked
    over, and building the voice plus a first chunk for each of them delayed
    the next live request (4 of them: 0.32 s -> 1.56 s).
    """
    t0 = time.perf_counter()
    if not await _acquire_lock_unless_gone(request):
        logger.info("tts/stream skipped: client left after %.0f ms in the queue", (time.perf_counter() - t0) * 1000)
        return Response(status_code=499)
    try:
        voice = await _prepare_voice(data, db)
        chunks = _synthesize(voice, data.text)
        try:
            first_audio, sample_rate = await chunks.__anext__()
        except StopAsyncIteration:
            first_audio, sample_rate = np.zeros(0, dtype=np.float32), 24000
    except BaseException:
        inference_lock.release()
        raise
    ttfb_ms = (time.perf_counter() - t0) * 1000
    logger.info("tts/stream first chunk after %.0f ms (%d samples)", ttfb_ms, len(first_audio))

    closed = False

    async def _cleanup():
        # Runs from the body's finally and as a background task, because the
        # body never starts when the client disconnects before the first send.
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            await chunks.aclose()
        finally:
            inference_lock.release()

    async def _body():
        try:
            if data.format == "wav":
                yield _streaming_wav_header(sample_rate)
            yield _to_pcm16(first_audio)
            async for audio, _sr in chunks:
                yield _to_pcm16(audio)
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            logger.debug("Client disconnected during /tts/stream")
        finally:
            await _cleanup()

    return StreamingResponse(
        _body(),
        background=BackgroundTask(_cleanup),
        media_type="audio/wav" if data.format == "wav" else "audio/pcm",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Channels": "1",
            "X-Sample-Format": "s16le",
            "X-First-Chunk-Ms": f"{ttfb_ms:.0f}",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tts/warmup")
async def tts_warmup(data: TTSStreamOptions, db: Session = Depends(get_db)):
    """Load the model, build the voice prompt and run one short generation."""
    t0 = time.perf_counter()
    async with inference_lock:
        voice = await _prepare_voice(data, db)
        samples = 0
        async for audio, _sr in _synthesize(voice, "Hi."):
            samples += len(audio)
    return {"engine": voice.engine, "model_size": data.model_size, "warmup_ms": round((time.perf_counter() - t0) * 1000)}


# ─── WebSocket ──────────────────────────────────────────────────────────

# A sentence is complete when its end punctuation is followed by whitespace.
_SENTENCE_END = re.compile(r"[.!?…。！？]+[\"')\]]*\s+")
_CLAUSE_END = re.compile(r"[,;:—]\s+")


def _take_ready_segments(buffer: str, soft_limit: int) -> Tuple[list, str]:
    """Split complete sentences off the front of ``buffer``."""
    ready = []
    while True:
        m = _SENTENCE_END.search(buffer)
        if m is None:
            break
        ready.append(buffer[: m.end()].strip())
        buffer = buffer[m.end():]
    if len(buffer) > soft_limit:
        cut = None
        for m in _CLAUSE_END.finditer(buffer):
            cut = m.end()
        if cut:
            ready.append(buffer[:cut].strip())
            buffer = buffer[cut:]
    return [s for s in ready if s], buffer


@router.websocket("/tts/ws")
async def tts_ws(ws: WebSocket):
    """Incremental text-to-speech over one WebSocket.

    Client → server (JSON text frames):
      {"type": "start", "profile_id": ..., "language": "en", ...TTSStreamOptions}
      {"type": "text", "text": "partial LLM output "}   buffered; complete sentences start at once
      {"type": "flush"}                                  speak the rest of the buffer now
      {"type": "cancel"}                                 stop speech, drop queued text (barge-in)
      {"type": "end"}                                    flush, finish, then close

    Server → client:
      {"type": "ready", "sample_rate", "channels", "format", "engine"}
      {"type": "segment_start", "id", "text"}
      binary frames: PCM16 little-endian mono audio of the current segment
      {"type": "segment_end", "id", "first_chunk_ms", "audio_ms"}
      {"type": "cancelled"} | {"type": "error", "message"} | {"type": "end"}
    """
    await ws.accept()
    voice: Optional[_Voice] = None
    pending: asyncio.Queue = asyncio.Queue()
    buffer = ""
    segment_id = 0
    worker: Optional[asyncio.Task] = None

    async def _send_json(payload: dict):
        await ws.send_text(json.dumps(payload))

    async def _speak_worker():
        while True:
            seg_id, text = await pending.get()
            if seg_id is None:
                await _send_json({"type": "end"})
                return
            await _send_json({"type": "segment_start", "id": seg_id, "text": text})
            t0 = time.perf_counter()
            first_ms = None
            samples = 0
            sample_rate = 24000
            async with inference_lock:
                stream = _synthesize(voice, text)
                try:
                    async for audio, sample_rate in stream:
                        if first_ms is None:
                            first_ms = (time.perf_counter() - t0) * 1000
                        samples += len(audio)
                        await ws.send_bytes(_to_pcm16(audio))
                finally:
                    await stream.aclose()
            await _send_json({
                "type": "segment_end",
                "id": seg_id,
                "first_chunk_ms": round(first_ms or 0),
                "audio_ms": round(samples * 1000 / sample_rate),
            })

    def _start_worker():
        nonlocal worker
        worker = asyncio.create_task(_speak_worker())

    async def _stop_worker():
        nonlocal worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except BaseException:
                pass
        worker = None

    def _enqueue(texts):
        nonlocal segment_id
        for t in texts:
            segment_id += 1
            pending.put_nowait((segment_id, t))

    try:
        while True:
            if worker is not None and worker.done():
                exc = worker.exception() if not worker.cancelled() else None
                if exc is not None:
                    await _send_json({"type": "error", "message": str(exc)})
                    _start_worker()
                elif not worker.cancelled():
                    break  # finished after "end"

            receive = asyncio.create_task(ws.receive_text())
            waiters = {receive} | ({worker} if worker is not None else set())
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if receive not in done:
                receive.cancel()
                continue
            try:
                msg = json.loads(receive.result())
            except json.JSONDecodeError:
                await _send_json({"type": "error", "message": "invalid JSON"})
                continue
            kind = msg.get("type")

            if kind == "start":
                await _stop_worker()
                try:
                    opts = TTSStreamOptions(**{k: v for k, v in msg.items() if k != "type"})
                    db = next(get_db())
                    try:
                        async with inference_lock:
                            voice = await _prepare_voice(opts, db)
                    finally:
                        db.close()
                except Exception as e:
                    detail = getattr(e, "detail", None) or str(e)
                    await _send_json({"type": "error", "message": detail})
                    continue
                pending = asyncio.Queue()
                buffer = ""
                _start_worker()
                await _send_json({
                    "type": "ready",
                    "engine": voice.engine,
                    "sample_rate": getattr(getattr(voice.backend, "model", None), "sample_rate", 24000),
                    "channels": 1,
                    "format": "s16le",
                })
            elif voice is None:
                await _send_json({"type": "error", "message": "send a start message first"})
            elif kind == "text":
                buffer += str(msg.get("text", ""))
                ready, buffer = _take_ready_segments(buffer, voice.opts.max_chunk_chars)
                _enqueue(ready)
            elif kind == "flush":
                _enqueue([buffer.strip()] if buffer.strip() else [])
                buffer = ""
            elif kind == "cancel":
                await _stop_worker()
                pending = asyncio.Queue()
                buffer = ""
                _start_worker()
                await _send_json({"type": "cancelled"})
            elif kind == "end":
                _enqueue([buffer.strip()] if buffer.strip() else [])
                buffer = ""
                pending.put_nowait((None, None))
            else:
                await _send_json({"type": "error", "message": f"unknown message type: {kind}"})
    except WebSocketDisconnect:
        logger.debug("Client disconnected from /tts/ws")
    finally:
        await _stop_worker()
        try:
            await ws.close()
        except Exception:
            pass
