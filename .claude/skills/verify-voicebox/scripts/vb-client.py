"""WebSocket and MCP clients for verify-voicebox. Run through `vb-verify client`, not directly.

Usage (vb-verify fills <out> and <base>):
  stt-ws <out> <base> <wav> [model]          stream a 16 kHz mono PCM16 WAV to /stt/ws
  tts-ws <out> <base> <profile_id> <text>    stream text to /tts/ws (qwen 0.6B)
  mcp    <out> <base> <tool> <json-args>     call an MCP tool at /mcp/
Writes <out>.events.jsonl (every server message) and, for tts-ws, <out>.wav.
"""

import asyncio
import json
import sys
import wave


def log_event(fh, event):
    fh.write(json.dumps(event) + "\n")
    print(json.dumps(event))


async def stt_ws(out, base, wav_path, model="whisper-base"):
    import websockets

    with wave.open(wav_path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono PCM16"
        pcm = w.readframes(w.getnframes())
    url = base.replace("http", "ws", 1) + "/stt/ws"
    with open(out + ".events.jsonl", "w") as fh:
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"type": "start", "model": model, "language": "en", "sample_rate": 16000, "vad": False}))
            log_event(fh, json.loads(await ws.recv()))
            step = 640  # 20 ms of 16 kHz PCM16
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i : i + step])
                await asyncio.sleep(0.02)  # real time, like a microphone
            await ws.send(json.dumps({"type": "end"}))
            async for msg in ws:
                event = json.loads(msg)
                log_event(fh, event)
                if event["type"] in ("end", "error"):
                    break


async def tts_ws(out, base, profile_id, text):
    import websockets

    url = base.replace("http", "ws", 1) + "/tts/ws"
    audio = bytearray()
    rate = 24000
    with open(out + ".events.jsonl", "w") as fh:
        async with websockets.connect(url, max_size=None) as ws:
            await ws.send(json.dumps({"type": "start", "profile_id": profile_id, "engine": "qwen", "model_size": "0.6B"}))
            await ws.send(json.dumps({"type": "text", "text": text}))
            await ws.send(json.dumps({"type": "end"}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    audio += msg
                    continue
                event = json.loads(msg)
                log_event(fh, event)
                if event["type"] == "ready":
                    rate = event["sample_rate"]
                if event["type"] in ("end", "error"):
                    break
    with wave.open(out + ".wav", "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(bytes(audio))
    print(f"audio: {out}.wav ({len(audio)} bytes, {rate} Hz)")


async def mcp_call(out, base, tool, args):
    from fastmcp import Client

    async with Client(base + "/mcp/") as client:
        tools = sorted(t.name for t in await client.list_tools())
        result = await client.call_tool(tool, json.loads(args))
    with open(out + ".events.jsonl", "w") as fh:
        log_event(fh, {"tools": tools})
        log_event(fh, {"tool": tool, "args": json.loads(args), "result": result.structured_content or str(result.content)})


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    fn = {"stt-ws": stt_ws, "tts-ws": tts_ws, "mcp": mcp_call}[cmd]
    asyncio.run(fn(*rest))
