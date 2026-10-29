"""LiveKit Agents STT plugin for Voicebox ``WS /stt/ws``.

Protocol: ``docs/research/stt-streaming-design.md`` sections 7 and 8.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import weakref
from dataclasses import dataclass

import aiohttp
import numpy as np
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    LanguageCode,
    NotGivenOr,
    stt,
    utils,
)
from livekit.agents.utils import AudioBuffer, is_given

from ._common import ensure_ws_url

logger = logging.getLogger("voicebox_livekit.stt")

STT_SAMPLE_RATE = 16000  # the server works at 16 kHz; sending it avoids a second resample
DEFAULT_BASE_URL = "http://127.0.0.1:17493"


@dataclass
class STTOptions:
    model: str | None
    language: str | None
    vad: bool
    interim: bool
    interim_interval_ms: int
    max_segment_s: float
    delay_ms: int | None
    base_url: str

    def start_message(self, *, language: str | None, vad: bool | None = None) -> dict:
        msg = {
            "type": "start",
            "sample_rate": STT_SAMPLE_RATE,
            "vad": self.vad if vad is None else vad,
            "interim": self.interim,
            "interim_interval_ms": self.interim_interval_ms,
            "max_segment_s": self.max_segment_s,
        }
        if self.model:
            msg["model"] = self.model
        if language:
            msg["language"] = language
        if self.delay_ms is not None:
            msg["delay_ms"] = self.delay_ms
        return msg


class VoiceboxSTT(stt.STT):
    def __init__(
        self,
        *,
        model: str | None = "qwen-asr-0.6B",
        language: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        vad: bool = True,
        interim: bool = True,
        interim_interval_ms: int = 500,
        max_segment_s: float = 30.0,
        delay_ms: int | None = None,
        interim_results: bool = False,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Local streaming STT through a Voicebox server.

        Args:
            model: Voicebox STT model name, e.g. ``qwen-asr-0.6B`` or ``whisper-turbo``.
                ``None`` uses the default model of the Voicebox capture settings.
            language: ISO code. ``None`` lets the model detect the language.
            base_url: Voicebox server URL (``http://`` or ``ws://``).
            vad: ``True``: the server VAD opens and closes segments. ``False``: segments
                close only when the agent flushes the stream (agent VAD / turn detector).
            interim: ask the server for interim text when the model can make it.
            delay_ms: Voxtral Realtime transcription delay (a multiple of 80 from 80 to
                1200, or 2400). Lower is faster with more errors. ``None`` uses the server
                default. Other models ignore it.
            interim_results: the value of ``capabilities.interim_results`` until the first
                stream gets the server ``ready`` message, which then sets it.
            http_session: aiohttp session; default is the job http session.
        """
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=interim_results,
                # Agent VAD end of speech sends end_segment: the server closes the
                # segment at once instead of after its own silence timeout.
                manual_flush=True,
            )
        )
        self._opts = STTOptions(
            model=model,
            language=language,
            vad=vad,
            interim=interim,
            interim_interval_ms=interim_interval_ms,
            max_segment_s=max_segment_s,
            delay_ms=delay_ms,
            base_url=base_url,
        )
        self._session = http_session
        self._streams = weakref.WeakSet[SpeechStream]()
        self._server_capabilities: dict | None = None

    @property
    def model(self) -> str:
        return self._opts.model or "default"

    @property
    def provider(self) -> str:
        return "voicebox"

    @property
    def server_capabilities(self) -> dict | None:
        """The ``capabilities`` of the last ``ready`` message (``None`` before the first stream)."""
        return self._server_capabilities

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def _on_ready(self, msg: dict) -> None:
        caps = msg.get("capabilities") or {}
        self._server_capabilities = caps
        self._capabilities.interim_results = bool(caps.get("interim_results")) and self._opts.interim

    async def _connect(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        url = ensure_ws_url(self._opts.base_url) + "/stt/ws"
        try:
            return await asyncio.wait_for(self._ensure_session().ws_connect(url), timeout)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Voicebox STT connection timed out") from e
        except aiohttp.WSServerHandshakeError as e:
            raise APIStatusError(f"Voicebox STT handshake failed: {e.message}", status_code=e.status) from e
        except (aiohttp.ClientError, OSError) as e:
            raise APIConnectionError(f"failed to connect to Voicebox at {url}") from e

    async def _start(self, ws: aiohttp.ClientWebSocketResponse, msg: dict, timeout: float) -> dict:
        """Send ``start`` and wait for ``ready``. A server ``error`` here is not retryable."""
        await ws.send_str(json.dumps(msg))
        try:
            reply = await asyncio.wait_for(ws.receive(), timeout)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Voicebox STT did not send ready") from e
        if reply.type != aiohttp.WSMsgType.TEXT:
            raise APIConnectionError(f"Voicebox STT closed before ready ({reply.type.name})")
        data = json.loads(reply.data)
        if data.get("type") == "error":
            # Unknown model, model not downloaded, bad options: a retry gives the same result.
            raise APIStatusError(f"Voicebox STT: {data.get('message')}", status_code=400, retryable=False)
        if data.get("type") != "ready":
            raise APIConnectionError(f"Voicebox STT: expected ready, got {data.get('type')}")
        self._on_ready(data)
        return data

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        """Batch recognition over the same WebSocket: all audio as 1 segment (no server VAD)."""
        lang = language if is_given(language) else self._opts.language
        frame = _to_mono_16k(rtc.combine_audio_frames(buffer))
        ws = await self._connect(conn_options.timeout)
        try:
            await self._start(ws, self._opts.start_message(language=lang, vad=False), conn_options.timeout)
            await ws.send_bytes(frame.data.tobytes())
            await ws.send_str(json.dumps({"type": "end"}))
            texts: list[str] = []
            detected: str | None = None
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = json.loads(msg.data)
                if data.get("type") == "final":
                    if data.get("text"):
                        texts.append(data["text"])
                    detected = data.get("language") or detected
                elif data.get("type") == "error":
                    raise APIStatusError(f"Voicebox STT: {data.get('message')}", status_code=500, retryable=False)
                elif data.get("type") == "end":
                    break
        finally:
            await ws.close()
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    language=LanguageCode(detected or lang or ""),
                    text=" ".join(texts),
                    end_time=frame.duration,
                )
            ],
        )

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        stream = SpeechStream(
            stt=self,
            language=language if is_given(language) else self._opts.language,
            conn_options=conn_options,
        )
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        for stream in list(self._streams):
            await stream.aclose()
        self._streams.clear()


class SpeechStream(stt.RecognizeStream):
    def __init__(self, *, stt: VoiceboxSTT, language: str | None, conn_options: APIConnectOptions) -> None:
        # sample_rate makes the base class resample every pushed frame to 16 kHz
        # with rtc.AudioResampler (and flush it on FlushSentinel).
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=STT_SAMPLE_RATE)
        self._vb_stt = stt
        self._language = language
        self._speaking = False

    async def _run(self) -> None:
        ws = await self._vb_stt._connect(self._conn_options.timeout)
        input_ended = False
        try:
            await self._vb_stt._start(
                ws, self._vb_stt._opts.start_message(language=self._language), self._conn_options.timeout
            )
            # Server times (start_s, end_s) count from the first audio sample sent.
            self.start_time = time.time()

            async def send_task() -> None:
                nonlocal input_ended
                async for item in self._input_ch:
                    if isinstance(item, rtc.AudioFrame):
                        await ws.send_bytes(_to_mono_16k(item).data.tobytes())
                    elif isinstance(item, self._FlushSentinel):
                        logger.debug("flush: end_segment")
                        await ws.send_str(json.dumps({"type": "end_segment"}))
                input_ended = True
                await ws.send_str(json.dumps({"type": "end"}))

            async def recv_task() -> None:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if self._handle_message(json.loads(msg.data)):
                            return
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise APIConnectionError("Voicebox STT connection lost") from ws.exception()
                if not input_ended:
                    raise APIConnectionError("Voicebox STT connection closed unexpectedly")

            tasks = [asyncio.create_task(send_task()), asyncio.create_task(recv_task())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()  # raise the first error
                await asyncio.gather(*tasks)
            finally:
                await utils.aio.gracefully_cancel(*tasks)
        except (aiohttp.ClientError, ConnectionError) as e:
            raise APIConnectionError("Voicebox STT connection error") from e
        finally:
            if self._speaking:
                # A stream that ends inside speech must not leave the agent in "user speaking".
                self._speaking = False
                self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
            with contextlib.suppress(Exception):
                await ws.close()

    def _handle_message(self, data: dict) -> bool:
        """Map 1 server message to LiveKit events. Returns True on ``end``."""
        logger.debug("server event: %s", data)
        kind = data.get("type")
        if kind == "speech_start":
            self._mark_provider_progress()
            self._speaking = True
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.START_OF_SPEECH,
                    speech_start_time=self._wall_time(data.get("start_s")),
                )
            )
        elif kind == "interim":
            if data.get("text"):
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                        request_id=str(data.get("segment_id", "")),
                        alternatives=[self._speech_data(data)],
                    )
                )
        elif kind == "final":
            self._mark_provider_progress()
            if data.get("text"):
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        request_id=str(data.get("segment_id", "")),
                        alternatives=[self._speech_data(data)],
                    )
                )
        elif kind == "speech_end":
            if self._speaking:
                self._speaking = False
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.END_OF_SPEECH,
                        speech_end_time=self._wall_time(data.get("end_s")),
                    )
                )
        elif kind == "error":
            # Decode error in 1 segment: the stream stays open for the next segment.
            logger.warning("Voicebox STT error: %s", data.get("message"))
        elif kind == "end":
            return True
        return False

    def _speech_data(self, data: dict) -> stt.SpeechData:
        return stt.SpeechData(
            language=LanguageCode(data.get("language") or self._language or ""),
            text=data.get("text", ""),
            start_time=(data.get("start_s") or 0.0) + self.start_time_offset,
            end_time=(data.get("end_s") or 0.0) + self.start_time_offset,
            confidence=1.0,
        )

    def _wall_time(self, stream_s: float | None) -> float | None:
        return None if stream_s is None else self.start_time + stream_s


def _to_mono_16k(frame: rtc.AudioFrame) -> rtc.AudioFrame:
    """Down-mix to mono. Frames from the stream are already 16 kHz (base class resampler)."""
    if frame.sample_rate != STT_SAMPLE_RATE:
        resampler = rtc.AudioResampler(frame.sample_rate, STT_SAMPLE_RATE, num_channels=frame.num_channels)
        frame = rtc.combine_audio_frames(resampler.push(frame) + resampler.flush())
    if frame.num_channels == 1:
        return frame
    pcm = np.frombuffer(frame.data, dtype=np.int16).reshape(-1, frame.num_channels)
    mono = pcm.mean(axis=1).astype(np.int16)
    return rtc.AudioFrame(mono.tobytes(), frame.sample_rate, 1, len(mono))
