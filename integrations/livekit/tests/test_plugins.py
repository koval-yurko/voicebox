"""VoiceboxSTT / VoiceboxTTS against fake /stt/ws and /tts/ws servers (aiohttp)."""

from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest
from aiohttp import WSMsgType, web
from livekit import rtc
from livekit.agents import APIStatusError, stt, utils

from voicebox_livekit import VoiceboxSTT, VoiceboxTTS

READY_CAPS = {
    "interim_results": True,
    "revises_text": True,
    "native_streaming": False,
    "min_latency_ms": 500,
    "needs_vad": False,
}


class FakeVoicebox:
    """Records what the plugin sends and answers like Voicebox."""

    def __init__(self, *, start_error: str | None = None, caps: dict | None = None):
        self.start_error = start_error
        self.caps = caps or READY_CAPS
        self.start_msgs: list[dict] = []
        self.controls: list[str] = []
        self.audio_bytes = 0
        self.tts_text: list[str] = []
        self.tts_closed = asyncio.Event()

    async def stt_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        segment = 0
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                self.audio_bytes += len(msg.data)
                continue
            data = json.loads(msg.data)
            kind = data["type"]
            if kind == "start":
                self.start_msgs.append(data)
                if self.start_error:
                    await ws.send_json({"type": "error", "message": self.start_error})
                    continue
                await ws.send_json({"type": "ready", "model": data.get("model"), "capabilities": self.caps})
                continue
            self.controls.append(kind)
            if kind == "end_segment":
                segment += 1
                await ws.send_json({"type": "speech_start", "segment_id": segment, "start_s": 0.5})
                await ws.send_json({"type": "interim", "segment_id": segment, "text": "hello", "stable_text": ""})
                await ws.send_json({"type": "speech_end", "segment_id": segment, "end_s": 1.5})
                await ws.send_json(
                    {
                        "type": "final",
                        "segment_id": segment,
                        "text": "hello world",
                        "language": "en",
                        "start_s": 0.5,
                        "end_s": 1.5,
                    }
                )
            elif kind == "end":
                if data.get("type") == "end" and self.audio_bytes and not segment:
                    # recognize(): 1 segment for all audio
                    await ws.send_json(
                        {"type": "final", "segment_id": 1, "text": "batch text", "language": "en", "start_s": 0, "end_s": 1}
                    )
                await ws.send_json({"type": "end"})
                break
        return ws

    async def tts_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        try:
            async for msg in ws:
                data = json.loads(msg.data)
                kind = data["type"]
                if kind == "start":
                    self.start_msgs.append(data)
                    await ws.send_json({"type": "ready", "sample_rate": 24000, "channels": 1, "format": "s16le"})
                elif kind == "text":
                    self.tts_text.append(data["text"])
                elif kind in ("flush", "end"):
                    self.controls.append(kind)
                    if self.tts_text:
                        await ws.send_json({"type": "segment_start", "id": 1, "text": "".join(self.tts_text)})
                        # 100 ms of audio per text message
                        for _ in self.tts_text:
                            await ws.send_bytes(np.zeros(2400, dtype=np.int16).tobytes())
                        await ws.send_json({"type": "segment_end", "id": 1})
                        self.tts_text = []
                    if kind == "end":
                        await ws.send_json({"type": "end"})
                        break
        finally:
            self.tts_closed.set()
        return ws


async def _serve(fake: FakeVoicebox):
    app = web.Application()
    app.router.add_get("/stt/ws", fake.stt_ws)
    app.router.add_get("/tts/ws", fake.tts_ws)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def _frame(sample_rate: int, ms: int) -> rtc.AudioFrame:
    n = sample_rate * ms // 1000
    t = np.arange(n) / sample_rate
    pcm = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
    return rtc.AudioFrame(pcm.tobytes(), sample_rate, 1, n)


async def test_stream_maps_events_and_resamples_to_16k():
    fake = FakeVoicebox()
    runner, url = await _serve(fake)
    try:
        async with utils.http_context.open():
            vb = VoiceboxSTT(model="qwen-asr-0.6B", language="en", base_url=url)
            assert vb.capabilities.streaming and vb.capabilities.manual_flush
            stream = vb.stream()
            for _ in range(10):  # 1 s of 48 kHz audio
                stream.push_frame(_frame(48000, 100))
            stream.flush()
            stream.end_input()
            events = [ev async for ev in stream]
    finally:
        await runner.cleanup()

    assert fake.start_msgs[0]["model"] == "qwen-asr-0.6B"
    assert fake.start_msgs[0]["sample_rate"] == 16000
    assert fake.start_msgs[0]["language"] == "en"
    assert fake.start_msgs[0]["vad"] is True
    # FlushSentinel → end_segment; end_input → its own flush, then end
    assert fake.controls == ["end_segment", "end_segment", "end"]
    # 1 s at 16 kHz PCM16 is 32000 bytes (resampler latency can hold a few samples)
    assert abs(fake.audio_bytes - 32000) < 1000
    assert vb.capabilities.interim_results is True

    types = [ev.type for ev in events]
    assert types[:4] == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
    ]
    final = events[3]
    assert final.alternatives[0].text == "hello world"
    assert final.alternatives[0].language == "en"
    assert final.alternatives[0].end_time == pytest.approx(1.5)
    assert events[0].speech_start_time is not None


async def test_interim_results_follow_server_capabilities():
    fake = FakeVoicebox(caps={**READY_CAPS, "interim_results": False})
    runner, url = await _serve(fake)
    try:
        async with utils.http_context.open():
            vb = VoiceboxSTT(base_url=url, interim_results=True)
            stream = vb.stream()
            stream.push_frame(_frame(16000, 100))
            stream.end_input()
            _ = [ev async for ev in stream]
    finally:
        await runner.cleanup()
    assert vb.capabilities.interim_results is False
    assert vb.server_capabilities == {**READY_CAPS, "interim_results": False}


async def test_start_error_is_not_retried():
    fake = FakeVoicebox(start_error="Qwen3-ASR 0.6B is not downloaded")
    runner, url = await _serve(fake)
    errors = []
    try:
        async with utils.http_context.open():
            vb = VoiceboxSTT(base_url=url)
            vb.on("error", errors.append)
            stream = vb.stream()
            stream.push_frame(_frame(16000, 100))
            with pytest.raises(APIStatusError, match="not downloaded"):
                _ = [ev async for ev in stream]
    finally:
        await runner.cleanup()
    assert len(fake.start_msgs) == 1  # no retry
    assert errors and errors[0].recoverable is False


async def test_recognize_sends_one_segment_without_vad():
    fake = FakeVoicebox()
    runner, url = await _serve(fake)
    try:
        async with utils.http_context.open():
            vb = VoiceboxSTT(base_url=url)
            event = await vb.recognize([_frame(16000, 500)])
    finally:
        await runner.cleanup()
    assert fake.start_msgs[0]["vad"] is False
    assert fake.audio_bytes == 16000  # 0.5 s at 16 kHz PCM16
    assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
    assert event.alternatives[0].text == "batch text"


async def test_tts_stream_sends_text_and_emits_audio():
    fake = FakeVoicebox()
    runner, url = await _serve(fake)
    try:
        async with utils.http_context.open():
            tts = VoiceboxTTS(profile_id="p1", base_url=url)
            stream = tts.stream()
            stream.push_text("Hello there. ")
            stream.push_text("How are you?")
            stream.end_input()
            frames = [ev.frame async for ev in stream]
    finally:
        await runner.cleanup()
    assert fake.start_msgs[0]["profile_id"] == "p1"
    assert fake.controls[-1] == "end"
    audio_s = sum(f.duration for f in frames)
    assert audio_s == pytest.approx(0.2, abs=0.01)
    assert all(f.sample_rate == 24000 for f in frames)


async def test_tts_aclose_closes_the_socket():
    """Barge-in: the agent closes the TTS stream; the server must see the socket close."""
    fake = FakeVoicebox()
    runner, url = await _serve(fake)
    try:
        async with utils.http_context.open():
            tts = VoiceboxTTS(profile_id="p1", base_url=url)
            stream = tts.stream()
            stream.push_text("A long answer that the user interrupts")
            await asyncio.sleep(0.2)
            await stream.aclose()
            await asyncio.wait_for(fake.tts_closed.wait(), 2)
    finally:
        await runner.cleanup()
