"""LiveKit Agents TTS plugin for Voicebox ``WS /tts/ws``.

The agent pushes LLM text as it arrives. The server speaks each complete
sentence at once and sends PCM16 audio frames back. Closing the stream (agent
barge-in) closes the WebSocket, and the server stops the generation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import weakref
from dataclasses import dataclass

import aiohttp
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)

from ._common import ensure_ws_url

logger = logging.getLogger("voicebox_livekit.tts")

DEFAULT_BASE_URL = "http://127.0.0.1:17493"
DEFAULT_SAMPLE_RATE = 24000  # Qwen TTS; the real rate comes from the server "ready" message


@dataclass
class TTSOptions:
    profile_id: str
    language: str
    engine: str | None
    model_size: str
    instruct: str | None
    interval_ms: int
    base_url: str

    def start_message(self) -> dict:
        msg = {
            "type": "start",
            "profile_id": self.profile_id,
            "language": self.language,
            "model_size": self.model_size,
            "interval_ms": self.interval_ms,
        }
        if self.engine:
            msg["engine"] = self.engine
        if self.instruct:
            msg["instruct"] = self.instruct
        return msg


class VoiceboxTTS(tts.TTS):
    def __init__(
        self,
        *,
        profile_id: str,
        language: str = "en",
        engine: str | None = None,
        model_size: str = "1.7B",
        instruct: str | None = None,
        interval_ms: int = 320,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        base_url: str = DEFAULT_BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Local streaming TTS through a Voicebox server.

        Args:
            profile_id: Voicebox voice profile ID (``GET /profiles``).
            language: ISO code of the speech.
            engine: TTS engine (``qwen``, ``kokoro``, ...); ``None`` uses the profile default.
            model_size: model size for engines with sizes (``1.7B`` or ``0.6B`` for Qwen).
            interval_ms: audio length per streamed chunk.
            sample_rate: output rate of the engine (24000 for Qwen).
        """
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._opts = TTSOptions(
            profile_id=profile_id,
            language=language,
            engine=engine,
            model_size=model_size,
            instruct=instruct,
            interval_ms=interval_ms,
            base_url=base_url,
        )
        self._session = http_session
        self._streams = weakref.WeakSet[SynthesizeStream]()

    @property
    def model(self) -> str:
        return f"{self._opts.engine or 'profile-default'}-{self._opts.model_size}"

    @property
    def provider(self) -> str:
        return "voicebox"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return self._synthesize_with_stream(text, conn_options=conn_options)

    def stream(self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> SynthesizeStream:
        stream = SynthesizeStream(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        for stream in list(self._streams):
            await stream.aclose()
        self._streams.clear()


class SynthesizeStream(tts.SynthesizeStream):
    def __init__(self, *, tts: VoiceboxTTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._vb_tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._vb_tts._opts
        url = ensure_ws_url(opts.base_url) + "/tts/ws"
        timeout = self._conn_options.timeout
        try:
            ws = await asyncio.wait_for(self._vb_tts._ensure_session().ws_connect(url), timeout)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Voicebox TTS connection timed out") from e
        except (aiohttp.ClientError, OSError) as e:
            raise APIConnectionError(f"failed to connect to Voicebox at {url}") from e

        request_id = utils.shortuuid()
        input_ended = False
        try:
            await ws.send_str(json.dumps(opts.start_message()))
            # Model load and voice prompt can take long on the first stream.
            ready = await asyncio.wait_for(ws.receive(), max(timeout, 120.0))
            if ready.type != aiohttp.WSMsgType.TEXT:
                raise APIConnectionError(f"Voicebox TTS closed before ready ({ready.type.name})")
            data = json.loads(ready.data)
            if data.get("type") == "error":
                raise APIStatusError(f"Voicebox TTS: {data.get('message')}", status_code=400, retryable=False)

            output_emitter.initialize(
                request_id=request_id,
                sample_rate=int(data.get("sample_rate") or self._vb_tts.sample_rate),
                num_channels=1,
                mime_type="audio/pcm",
                stream=True,
            )
            output_emitter.start_segment(segment_id=request_id)

            async def send_task() -> None:
                nonlocal input_ended
                async for item in self._input_ch:
                    if isinstance(item, self._FlushSentinel):
                        await ws.send_str(json.dumps({"type": "flush"}))
                    else:
                        self._mark_started()
                        await ws.send_str(json.dumps({"type": "text", "text": item}))
                input_ended = True
                await ws.send_str(json.dumps({"type": "end"}))

            async def recv_task() -> None:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        output_emitter.push(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(msg.data)
                        kind = event.get("type")
                        if kind == "end":
                            output_emitter.end_segment()
                            return
                        if kind == "error":
                            raise APIStatusError(
                                f"Voicebox TTS: {event.get('message')}", status_code=500, retryable=False
                            )
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise APIConnectionError("Voicebox TTS connection lost") from ws.exception()
                if not input_ended:
                    raise APIConnectionError("Voicebox TTS connection closed unexpectedly")
                output_emitter.end_segment()

            tasks = [asyncio.create_task(send_task()), asyncio.create_task(recv_task())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                for task in done:
                    task.result()
                await asyncio.gather(*tasks)
            finally:
                await utils.aio.gracefully_cancel(*tasks)
        except asyncio.TimeoutError as e:
            raise APITimeoutError("Voicebox TTS did not send ready") from e
        except (aiohttp.ClientError, ConnectionError) as e:
            raise APIConnectionError("Voicebox TTS connection error") from e
        finally:
            # Barge-in cancels this task: closing the socket stops the server generation.
            with contextlib.suppress(Exception):
                await ws.close()
