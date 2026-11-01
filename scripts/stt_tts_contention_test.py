#!/usr/bin/env python3
"""Measure STT delay while TTS streams: 1 server process against 2.

Usage:
  backend/venv/bin/python scripts/stt_tts_contention_test.py --wav speech.wav --profile <id> \
      --stt-url http://127.0.0.1:17595 --tts-url http://127.0.0.1:17594 [--rounds 6] [--no-tts]

Each round sends the WAV to WS /stt/ws in real time (20 ms frames, vad off),
then end_segment, and times end_segment -> final. With TTS on, 1 task keeps
/tts/ws (or POST /tts/stream) busy the whole time and reads audio as fast as it comes.
Point --stt-url and --tts-url at 1 port for the 1-process case.
"""

import argparse
import asyncio
import json
import statistics
import time
import wave

import httpx
import numpy as np
import websockets

TTS_TEXT = (
    "This is a long answer from the voice agent. It keeps talking so that the speech model "
    "has work to do the whole time. We measure how long the transcription waits. "
) * 4


def load_pcm16_16k(path):
    with wave.open(path, "rb") as w:
        rate, raw = w.getframerate(), w.readframes(10**9)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio.astype(np.int16).tobytes()


async def stt_round(url, model, pcm):
    frame = 640  # 20 ms of PCM16 at 16 kHz
    async with websockets.connect(url.replace("http", "ws", 1) + "/stt/ws", max_size=None) as ws:
        await ws.send(json.dumps({"type": "start", "model": model, "language": "en", "vad": False, "interim": True}))
        ready = json.loads(await ws.recv())
        assert ready["type"] == "ready", ready
        interims = []
        final = {}

        async def reader():
            async for msg in ws:
                ev = json.loads(msg)
                if ev["type"] == "interim":
                    interims.append(time.perf_counter())
                elif ev["type"] == "final":
                    final["t"], final["text"] = time.perf_counter(), ev["text"]
                    return
                elif ev["type"] == "error":
                    raise RuntimeError(ev["message"])

        task = asyncio.create_task(reader())
        nxt = time.perf_counter()
        for i in range(0, len(pcm) - frame + 1, frame):
            await ws.send(pcm[i : i + frame])
            nxt += 0.02
            await asyncio.sleep(max(0.0, nxt - time.perf_counter()))
        t_end = time.perf_counter()
        await ws.send(json.dumps({"type": "end_segment"}))
        await asyncio.wait_for(task, 60)
        gaps = [b - a for a, b in zip(interims, interims[1:])]
        return final["t"] - t_end, len(interims), max(gaps, default=0.0), final["text"]


async def tts_load_ws(url, profile, stop, stats):
    """Like the LiveKit plugin: 1 WebSocket, the server splits the text into sentences."""
    while not stop.is_set():
        t0 = time.perf_counter()
        n = 0
        async with websockets.connect(url.replace("http", "ws", 1) + "/tts/ws", max_size=None) as ws:
            await ws.send(json.dumps({"type": "start", "profile_id": profile}))
            ready = json.loads(await ws.recv())
            assert ready["type"] == "ready", ready
            await ws.send(json.dumps({"type": "text", "text": TTS_TEXT}))
            await ws.send(json.dumps({"type": "end"}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    n += len(msg)
                elif json.loads(msg)["type"] in ("end", "error") or stop.is_set():
                    break
        stats.append((n / 2 / ready["sample_rate"], time.perf_counter() - t0))


async def tts_load(url, profile, stop, stats):
    async with httpx.AsyncClient(timeout=None) as client:
        while not stop.is_set():
            t0 = time.perf_counter()
            n = 0
            body = {"profile_id": profile, "text": TTS_TEXT}
            async with client.stream("POST", url + "/tts/stream", json=body) as r:
                r.raise_for_status()
                rate = int(r.headers["x-sample-rate"])
                async for chunk in r.aiter_bytes():
                    n += len(chunk)
                    if stop.is_set():
                        break
            stats.append((n / 2 / rate, time.perf_counter() - t0))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wav", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--stt-url", default="http://127.0.0.1:17593")
    p.add_argument("--tts-url", default="http://127.0.0.1:17593")
    p.add_argument("--stt-model", default="qwen-asr-0.6B")
    p.add_argument("--rounds", type=int, default=6)
    p.add_argument("--no-tts", action="store_true")
    p.add_argument("--tts-route", choices=["ws", "http"], default="ws", help="/tts/ws (LiveKit) or /tts/stream")
    args = p.parse_args()

    pcm = load_pcm16_16k(args.wav)
    await stt_round(args.stt_url, args.stt_model, pcm)  # warm up: model load, first compile

    stop, tts_stats = asyncio.Event(), []
    load = None
    if not args.no_tts:
        load_fn = tts_load_ws if args.tts_route == "ws" else tts_load
        load = asyncio.create_task(load_fn(args.tts_url, args.profile, stop, tts_stats))
        await asyncio.sleep(3)  # let TTS reach steady decoding

    rows = []
    for i in range(args.rounds):
        delay, n_int, gap, text = await stt_round(args.stt_url, args.stt_model, pcm)
        rows.append((delay, gap))
        print(f"round {i + 1}: final {delay * 1000:6.0f} ms  interims {n_int:2d}  max gap {gap * 1000:5.0f} ms  {text!r}")

    stop.set()
    if load:
        await load
    finals = [r[0] * 1000 for r in rows]
    gaps = [r[1] * 1000 for r in rows]
    print(f"final delay ms: median {statistics.median(finals):.0f}  max {max(finals):.0f}")
    print(f"max interim gap ms: median {statistics.median(gaps):.0f}  max {max(gaps):.0f}")
    if tts_stats:
        audio, wall = sum(a for a, _ in tts_stats), sum(w for _, w in tts_stats)
        print(f"TTS: {audio:.1f} s audio in {wall:.1f} s wall, real-time factor {audio / wall:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
