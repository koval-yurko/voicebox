"""End-to-end check of a local AgentSession with no LiveKit server and no mic.

A fake mic sends a WAV file in real time; a fake speaker "plays" the agent audio
in real time. The session uses the example EchoAgent with VoiceboxSTT,
VoiceboxTTS and Silero VAD, so all audio goes through the real Voicebox servers.

1. The user says sentence 1. Check: a FINAL transcript, then the agent speaks.
2. 1.5 s into the agent reply, the user says sentence 2 (barge-in).
   Check: the agent speech stops (interrupted), the time it took, and a FINAL
   transcript of sentence 2.

    uv run python scripts/e2e_session.py --stt-port 17499 --tts-port 17493 path/to/speech_pause_speech_16k.wav
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from livekit import rtc
from livekit.agents import AgentSession, utils
from livekit.agents.voice import io
from livekit.plugins import silero

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from voice_agent import EchoAgent  # noqa: E402

from voicebox_livekit import VoiceboxSTT, VoiceboxTTS  # noqa: E402

RATE = 16000
FRAME = RATE // 50  # 20 ms


class FakeMic(io.AudioInput):
    """20 ms frames in real time: queued speech, else silence."""

    def __init__(self) -> None:
        super().__init__(label="fake-mic")
        self._queue: list[np.ndarray] = []
        self._next = time.monotonic()
        self.idle = asyncio.Event()
        self.idle.set()

    def say(self, pcm: np.ndarray) -> None:
        self.idle.clear()
        self._queue += [pcm[i : i + FRAME] for i in range(0, len(pcm) - FRAME + 1, FRAME)]

    async def __anext__(self) -> rtc.AudioFrame:
        self._next += 0.02
        await asyncio.sleep(max(0.0, self._next - time.monotonic()))
        if self._queue:
            chunk = self._queue.pop(0)
        else:
            self.idle.set()
            chunk = np.zeros(FRAME, dtype=np.int16)
        return rtc.AudioFrame(chunk.tobytes(), RATE, 1, FRAME)


class FakeSpeaker(io.AudioOutput):
    """Plays at real-time speed: a segment ends at first frame time + its audio length."""

    def __init__(self) -> None:
        super().__init__(label="fake-speaker", capabilities=io.AudioOutputCapabilities(pause=False))
        self._pushed = 0.0
        self._start = 0.0
        self._done_task: asyncio.Task | None = None
        self._cleared = asyncio.Event()
        self.clears = 0

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if not self._pushed:
            self._start = time.monotonic()
            self.on_playback_started(created_at=time.time())
        self._pushed += frame.duration

    def flush(self) -> None:
        super().flush()
        if self._pushed:
            self._cleared.clear()
            self._done_task = asyncio.create_task(self._play())

    def clear_buffer(self) -> None:
        self.clears += 1
        if self._pushed:
            self._cleared.set()
            if self._done_task is None:  # cleared before flush: finish the segment now
                self._done_task = asyncio.create_task(self._play())

    async def _play(self) -> None:
        remaining = self._start + self._pushed - time.monotonic()
        with_clear = asyncio.create_task(self._cleared.wait())
        done, _ = await asyncio.wait([with_clear], timeout=max(0.0, remaining))
        interrupted = bool(done)
        with_clear.cancel()
        position = min(self._pushed, time.monotonic() - self._start)
        self._pushed = 0.0
        self._done_task = None
        self.on_playback_finished(playback_position=position, interrupted=interrupted)


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def first_profile(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/profiles", timeout=10) as resp:
        return json.load(resp)[0]["id"]


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("wav", help="16 kHz mono WAV: sentence, pause, sentence")
    p.add_argument("--split-s", type=float, default=4.0, help="time inside the pause between the 2 sentences")
    p.add_argument("--stt-port", type=int, default=17493)
    p.add_argument("--tts-port", type=int, default=17493)
    p.add_argument("--model", default="qwen-asr-0.6B")
    p.add_argument("--profile-id")
    p.add_argument("--debug", action="store_true", help="log the plugin WebSocket traffic")
    args = p.parse_args()
    if args.debug:
        import logging

        logging.basicConfig(format="%(relativeCreated)7.0fms %(name)s %(message)s")
        logging.getLogger("voicebox_livekit").setLevel(logging.DEBUG)

    pcm = read_wav(args.wav)
    split = int(args.split_s * RATE)
    sentence1, sentence2 = pcm[:split], pcm[split:]

    t0 = time.monotonic()
    log: list[str] = []

    def note(msg: str) -> None:
        line = f"{time.monotonic() - t0:6.2f}s  {msg}"
        log.append(line)
        print(line, flush=True)

    finals: list[str] = []
    interrupted_items: list[str] = []
    speaking = asyncio.Event()
    stopped = asyncio.Event()
    stop_time: list[float] = []  # agent speaking -> other state

    async with utils.http_context.open():
        session = AgentSession(
            stt=VoiceboxSTT(model=args.model, base_url=f"http://127.0.0.1:{args.stt_port}"),
            tts=VoiceboxTTS(
                profile_id=args.profile_id or first_profile(args.tts_port),
                base_url=f"http://127.0.0.1:{args.tts_port}",
            ),
            vad=silero.VAD.load(),
            # The fake speaker makes no echo. The default (3 s) sends silence to the
            # STT and blocks barge-in for the first 3 s of each agent reply.
            aec_warmup_duration=None,
        )
        mic, speaker = FakeMic(), FakeSpeaker()
        session.input.audio = mic
        session.output.audio = speaker

        @session.on("user_input_transcribed")
        def _transcript(ev) -> None:
            if ev.is_final:
                finals.append(ev.transcript)
                note(f"FINAL transcript: {ev.transcript}")

        @session.on("user_state_changed")
        def _user(ev) -> None:
            note(f"user state: {ev.old_state} -> {ev.new_state}")

        @session.on("agent_state_changed")
        def _agent(ev) -> None:
            note(f"agent state: {ev.old_state} -> {ev.new_state}")
            if ev.new_state == "speaking":
                speaking.set()
            elif ev.old_state == "speaking":
                stop_time.append(time.monotonic())
                stopped.set()

        @session.on("conversation_item_added")
        def _item(ev) -> None:
            item = ev.item
            if getattr(item, "role", None) == "assistant":
                note(f"agent said (interrupted={item.interrupted}): {item.text_content}")
                if item.interrupted:
                    interrupted_items.append(item.text_content or "")

        await session.start(agent=EchoAgent())
        await asyncio.sleep(1.0)

        note("user: sentence 1")
        mic.say(sentence1)
        await asyncio.wait_for(speaking.wait(), 60)
        speaking.clear()
        stopped.clear()
        await asyncio.sleep(1.5)

        note("user: sentence 2 (barge-in)")
        barge_t = time.monotonic()
        mic.say(sentence2)
        try:
            await asyncio.wait_for(stopped.wait(), 10)
        except asyncio.TimeoutError:
            note("agent did not stop")
        # Let sentence 2 finish and its echo reply start, then close.
        await mic.idle.wait()
        await asyncio.sleep(4.0)
        await session.aclose()

    stops_after = [t for t in stop_time if t > barge_t]
    stop_ms = (stops_after[0] - barge_t) * 1000 if stops_after else None
    ok_transcript = any("first sentence" in f.lower() for f in finals) and any(
        "second part" in f.lower() for f in finals
    )
    ok_bargein = bool(interrupted_items) and stop_ms is not None
    print("\n== result")
    print(f"final transcripts: {finals}")
    print(f"interrupted agent speech: {len(interrupted_items)}; speaker clear_buffer calls: {speaker.clears}")
    print(f"agent stopped {stop_ms:.0f} ms after the barge-in audio started" if stop_ms else "agent did not stop")
    print(f"transcription: {'PASS' if ok_transcript else 'FAIL'}; barge-in: {'PASS' if ok_bargein else 'FAIL'}")
    return 0 if ok_transcript and ok_bargein else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
