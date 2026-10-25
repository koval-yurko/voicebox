#!/usr/bin/env python3
"""Measure real-time streaming from /tts/stream and /tts/ws.

Usage:
  backend/venv/bin/python scripts/tts_stream_test.py --profile <id> [--mode http|ws|both]
      [--url http://127.0.0.1:17493] [--api-key KEY] [--interval-ms 320] [--model-size 1.7B] [--out /tmp/tts]

--url may be an https:// address (for example a Cloudflare Tunnel hostname);
--api-key is sent as "Authorization: Bearer KEY" on both routes.

Prints time to first audio, total time and audio length, and writes WAV files.
"""

import argparse
import asyncio
import http.client
import json
import time
import wave
from urllib.parse import urlparse

TEXT = (
    "Hello there. This is a quick latency test for the local voice agent. "
    "We want the first audio as fast as possible."
)


def save_wav(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def auth_headers(args):
    return {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}


def run_http(args):
    u = urlparse(args.url)
    body = json.dumps({
        "profile_id": args.profile, "text": args.text, "interval_ms": args.interval_ms,
        "model_size": args.model_size, "language": args.language,
    })
    if u.scheme == "https":
        conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=600)
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=600)
    t0 = time.perf_counter()
    conn.request("POST", "/tts/stream", body=body,
                 headers={"content-type": "application/json", **auth_headers(args)})
    resp = conn.getresponse()
    if resp.status != 200:
        raise SystemExit(f"HTTP {resp.status}: {resp.read().decode()}")
    rate = int(resp.getheader("X-Sample-Rate", "24000"))
    t_headers = time.perf_counter() - t0
    pcm, first, chunks = bytearray(), None, 0
    while True:
        part = resp.read1(65536)
        if not part:
            break
        if first is None:
            first = time.perf_counter() - t0
        chunks += 1
        pcm += part
    total = time.perf_counter() - t0
    audio_s = len(pcm) / 2 / rate
    print(f"[http] headers {t_headers*1000:.0f} ms | first audio {first*1000:.0f} ms | total {total*1000:.0f} ms | "
          f"audio {audio_s:.2f} s | reads {chunks} | server first chunk {resp.getheader('X-First-Chunk-Ms')} ms")
    save_wav(f"{args.out}_http.wav", bytes(pcm), rate)


async def run_ws(args):
    import websockets

    url = args.url.replace("http", "ws", 1) + "/tts/ws"
    async with websockets.connect(url, max_size=None, additional_headers=auth_headers(args)) as ws:
        await ws.send(json.dumps({
            "type": "start", "profile_id": args.profile, "interval_ms": args.interval_ms,
            "model_size": args.model_size, "language": args.language,
        }))
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            raise SystemExit(f"ws start failed: {ready}")
        rate = ready["sample_rate"]

        t0 = time.perf_counter()

        async def feed():
            # Simulate an LLM token stream: one word every 40 ms.
            for word in args.text.split(" "):
                await ws.send(json.dumps({"type": "text", "text": word + " "}))
                await asyncio.sleep(0.04)
            await ws.send(json.dumps({"type": "end"}))

        feeder = asyncio.create_task(feed())
        pcm, first = bytearray(), None
        async for msg in ws:
            if isinstance(msg, bytes):
                if first is None:
                    first = time.perf_counter() - t0
                pcm += msg
                continue
            event = json.loads(msg)
            print(f"[ws] {(time.perf_counter()-t0)*1000:6.0f} ms  {msg}")
            if event["type"] in ("end", "error"):
                break
        await feeder
        total = time.perf_counter() - t0
        print(f"[ws] first audio {first*1000 if first else -1:.0f} ms after first word | total {total*1000:.0f} ms | "
              f"audio {len(pcm)/2/rate:.2f} s")
        save_wav(f"{args.out}_ws.wav", bytes(pcm), rate)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--url", default="http://127.0.0.1:17493")
    p.add_argument("--api-key", default=None)
    p.add_argument("--mode", choices=["http", "ws", "both"], default="both")
    p.add_argument("--interval-ms", type=int, default=320)
    p.add_argument("--model-size", default="1.7B")
    p.add_argument("--language", default="en")
    p.add_argument("--text", default=TEXT)
    p.add_argument("--out", default="/tmp/tts")
    args = p.parse_args()
    if args.mode in ("http", "both"):
        run_http(args)
    if args.mode in ("ws", "both"):
        asyncio.run(run_ws(args))


if __name__ == "__main__":
    main()
