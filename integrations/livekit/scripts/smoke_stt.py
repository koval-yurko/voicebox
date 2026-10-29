"""Stream a WAV file through VoiceboxSTT and print the LiveKit speech events.

The audio goes in as 20 ms frames at --input-rate (48 kHz like a LiveKit room),
in real time, so the plugin resampler and the server VAD see live-like input.

    uv run python scripts/smoke_stt.py --port 17493 path/to/speech.wav
"""

from __future__ import annotations

import argparse
import asyncio
import time
import wave

from livekit import rtc
from livekit.agents import stt, utils

from voicebox_livekit import VoiceboxSTT


def read_wav(path: str, rate: int) -> list[rtc.AudioFrame]:
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2, "PCM16 WAV only"
        frame = rtc.AudioFrame(w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getnframes())
    if frame.sample_rate != rate:
        resampler = rtc.AudioResampler(frame.sample_rate, rate, num_channels=frame.num_channels)
        frame = rtc.combine_audio_frames(resampler.push(frame) + resampler.flush())
    step = rate // 50  # 20 ms
    data = frame.data
    return [
        rtc.AudioFrame(data[i : i + step * frame.num_channels].tobytes(), rate, frame.num_channels, step)
        for i in range(0, len(data) - step * frame.num_channels + 1, step * frame.num_channels)
    ]


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav")
    p.add_argument("--port", type=int, default=17493)
    p.add_argument("--model", default="qwen-asr-0.6B")
    p.add_argument("--language", default="en")
    p.add_argument("--input-rate", type=int, default=48000)
    p.add_argument("--fast", action="store_true", help="push all audio at once")
    p.add_argument("--no-vad", action="store_true", help="client endpointing: 1 flush at the end")
    args = p.parse_args()

    frames = read_wav(args.wav, args.input_rate)
    # 1 s of silence at the end so the server VAD can close the last segment
    silence = rtc.AudioFrame(bytes(args.input_rate // 50 * 2), args.input_rate, 1, args.input_rate // 50)
    frames += [silence] * 50

    async with utils.http_context.open():
        vb = VoiceboxSTT(
            model=args.model, language=args.language, base_url=f"http://127.0.0.1:{args.port}", vad=not args.no_vad
        )
        stream = vb.stream()
        t0 = time.perf_counter()

        async def push() -> None:
            for frame in frames:
                stream.push_frame(frame)
                if not args.fast:
                    await asyncio.sleep(0.02)
            stream.end_input()

        pusher = asyncio.create_task(push())
        finals = 0
        async for ev in stream:
            text = ev.alternatives[0].text if ev.alternatives else ""
            print(f"{time.perf_counter() - t0:6.2f}s  {ev.type.name:20s} {text}")
            finals += ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT
        await pusher
        print(f"server capabilities: {vb.server_capabilities}")
        print(f"finals: {finals}")
        if not finals:
            raise SystemExit("no FINAL_TRANSCRIPT")


if __name__ == "__main__":
    asyncio.run(main())
