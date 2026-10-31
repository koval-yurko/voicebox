# Ticket: Run TTS, STT and LLM in separate processes on MLX

Status: research done, not started. Date: 2026-10-31. Branch: `custom/main`.

## Problem

On MLX, all MLX work runs on 1 thread (`_mlx_executor`). TTS and STT wait for each other in 1 queue. A long TTS stream can delay STT by about 1 chunk.

## Why not 2 threads in 1 process

- MLX keeps GPU streams per OS thread. Use of a stream from a different thread fails with `There is no Stream(gpu, N) in current thread` (upstream fix `f135a47`).
- `mlx_lm` and `mlx_audio.stt` make a global stream at import time. The stream belongs to the thread that imports the module first. Python imports a module 1 time per process:
  - `mlx_lm/generate.py:222`
  - `mlx_audio/stt/generate.py:224`
  - `generation_stream = mx.new_stream(mx.default_device())`
- Qwen3-TTS and Qwen3-ASR both use `mlx_lm`, so they share that stream.
- `empty_mlx_cache()` (`backend/backends/base.py:227`) clears the memory pool for the whole process.

## Current code (commit `826cde1`)

- `backend/backends/mlx_backend.py:21`: `_mlx_executor = ThreadPoolExecutor(max_workers=1)` and `_run_on_mlx_thread()`.
- `backend/backends/stt_streaming.py:181`: `get_model_executor()` returns `_mlx_executor` on MLX. Other backends get 1 thread per model.
- `backend/backends/mlx_backend.py` `generate_stream`: each TTS decode step is 1 job in the queue, so STT can run between chunks. The code decodes 1 chunk in advance.

## Hardware (dev Mac)

| Item | Value |
|---|---|
| Model | MacBook Pro, Apple M4 Max |
| CPU | 16 cores (12 performance, 4 efficiency) |
| GPU | 1 GPU with 40 cores, Metal 4 |
| Memory | 48 GB unified. MLX recommended working set is about 40 GB. |

The GPU is 1 device, but Metal can run command queues from different processes at the same time. Small decode steps often do not use all 40 cores, so 2 processes can partly overlap. Speed gain is not measured.

## Proposal

Run 1 Voicebox server process for each role, with a different port for each:

| Process | Port (example) | Job |
|---|---|---|
| TTS | 17494 | `/tts/*` |
| STT | 17495 | `/stt/*` |
| LLM | 17496 | `mlx_lm.server` or other OpenAI-compatible server |
| Orchestration | none | LiveKit agent (already a separate process, no inference) |

## What already works (no code change)

1. `backend/server.py:277-294` accepts `--port` and `--data-dir`. Each process gets its own MLX runtime, streams and `mlx-worker` thread.
2. LiveKit plugins take separate URLs: `VoiceboxSTT(base_url=...)` (`integrations/livekit/voicebox_livekit/stt.py:76`), `VoiceboxTTS(base_url=...)` (`tts.py:72`).
3. Models load on the first request, not at startup. A TTS process does not load STT models.
4. The LLM can be an external OpenAI-compatible endpoint (`OpenAICompatLLMBackend`, `backend/services/llm.py`).
5. 2 processes can share 1 data directory: SQLite uses WAL and `busy_timeout=5000` (`backend/database/session.py:53-54`).

## Problems to fix

1. **Startup cleanup marks live jobs as failed.** `backend/app.py:295-300` runs `UPDATE generations SET status='failed' WHERE status IN ('generating','loading_model')`. A restart of 1 process fails the live jobs of the other process. Fix: add a flag to skip this cleanup, or mark only rows owned by this process.
2. **The desktop app knows only 1 server URL.** The Tauri app starts 1 server. The UI does not route to other ports. Fix: a reverse proxy on 1 port, or URL settings for each role in the app.
3. **Memory.** Each process loads Python, MLX and about 1 GB of other libraries, plus its own models. The total must stay under about 40 GB.
4. **Settings in memory.** Each process keeps its own selected model and model cache. A setting changed through 1 port does not reach the other process at once. Not checked for each setting.

## Tasks

- [ ] Start 2 servers (TTS, STT) on 2 ports with 1 shared `--data-dir`.
- [ ] Point the LiveKit agent STT and TTS to the 2 ports.
- [ ] Measure STT delay during TTS streaming: 1 process against 2 processes (`integrations/livekit/scripts/e2e_session.py`).
- [ ] Measure memory for each process.
- [ ] Fix problem 1 (startup cleanup).
- [ ] Decide on problem 2 (proxy or app settings).
- [ ] Check settings that are kept in memory (problem 4).
- [ ] Later: move the LLM to its own process.

## Not checked

- Nothing was started or measured. Speed gain and memory use are unknown.
- Settings sync between processes is not verified.
