# Ticket: Run TTS and STT in separate processes on MLX

Status: research final. Step 1 (remove the built-in LLM) done 2026-11-01; steps 2-8 not started. Date: 2026-10-31. Branch: `custom/main`.

## Decisions

| # | Decision | Reason |
|---|---|---|
| 1 | Voicebox has 3 processes: **main**, **TTS worker** (`--role tts`), **STT worker** (`--role stt`). | Measured: in 1 process STT waits about 8 s during TTS. In 2 processes it waits about 240 ms. |
| 2 | **Main** owns all state: UI API, DB, files, settings, job queue, model downloads, events. Workers own no state. | Most multi-process problems come from 2 processes that both own state (see "Problems and how the design solves them"). |
| 3 | The UI keeps 1 URL (main, 17593). It never talks to a worker. | No UI routing refactor and no settings sync. |
| 4 | The voice agent (LiveKit) talks to the workers directly. | Lowest delay. The plugins already take separate URLs. |
| 5 | Remove the **built-in** LLM (local Qwen3 0.6B/1.7B/4B). Keep refine and personality, but only through the custom OpenAI-compatible endpoint. | The built-in LLM was made for small text tasks (1 turn, no streaming, no tool calls). It is not usable for a voice agent, and it makes the feature set unclear. |
| 6 | No `llm` role in Voicebox. A voice agent LLM runs in a separate app (LM Studio, Ollama or `mlx_lm.server`). | These tools already have streaming, chat and tool calls. Writing them again in Voicebox gives no value. |
| 7 | `--role all` (the default) keeps today's 1-process behavior. | Docker, Windows, Linux and upstream keep working. The fork stays close to upstream for rebases. |

## Problem

On MLX, all MLX work in 1 process runs on 1 thread (`_mlx_executor`, `backend/backends/mlx_backend.py:21`). Also 1 `asyncio.Lock` (`inference_lock`, `backend/services/task_queue.py:32`) lets only 1 TTS or STT job run at a time. TTS holds the lock for 1 full segment, so STT waits for 1 full TTS segment.

## Why not 2 threads in 1 process

- MLX keeps GPU streams per OS thread. Use of a stream from a different thread fails with `There is no Stream(gpu, N) in current thread` (upstream fix `f135a47`).
- `mlx_lm` and `mlx_audio.stt` make a global stream at import time. The stream belongs to the thread that imports the module first. Python imports a module 1 time per process:
  - `mlx_lm/generate.py:222`
  - `mlx_audio/stt/generate.py:224`
  - `generation_stream = mx.new_stream(mx.default_device())`
- Qwen3-TTS and Qwen3-ASR both use `mlx_lm`, so they share that stream.
- `empty_mlx_cache()` (`backend/backends/base.py:227`) clears the memory pool for the whole process.

## Measurements (2026-10-31, commit `848c7f5`)

Setup: 2 dev servers from this branch (`python -m backend.main --port N --data-dir /tmp/vb-mp/data`), 1 shared copy of the app data directory. TTS: Qwen3-TTS 1.7B bf16, profile "Men 1". STT: `qwen-asr-0.6B` over `WS /stt/ws`, `vad=false`, interim every 500 ms. STT input: a 5.6 s sentence, sent in real time. The installed Voicebox app was also running on 17493 (idle).

Script: `scripts/stt_tts_contention_test.py`. 1 warm-up round, then 6 rounds. The TTS load task keeps TTS busy all the time and reads audio as fast as it comes (worst case).

```
backend/venv/bin/python scripts/stt_tts_contention_test.py --wav /tmp/vb-mp/speech24k.wav \
    --profile <id> --stt-url http://127.0.0.1:17595 --tts-url http://127.0.0.1:17594 [--tts-route ws|http] [--no-tts]
```

### STT delay (end of speech to FINAL)

| Case | TTS load | Final delay median | Final delay max | Interims (of 13) | Max interim gap |
|---|---|---|---|---|---|
| STT only | none | 131 ms | 142 ms | 13 | 520 ms |
| 2 processes | `/tts/ws` | 242 ms | 546 ms | 12-13 | 762 ms |
| 2 processes | `/tts/stream` | 236 ms | 259 ms | 13 | 545 ms (max 808) |
| 1 process | `/tts/ws` | **7954 ms** | 8633 ms | 1 | - |
| 1 process | `/tts/stream` | **no FINAL in 60 s** (timeout) | - | - | - |

### TTS speed

| Case | Real-time factor (audio s / wall s) |
|---|---|
| TTS only | 0.92x |
| TTS with STT in the other process | 0.86x |
| TTS with STT in the same process | 0.91x (STT does not run, so TTS is not slowed) |

Each case ran 1 time for about 45 s. The 7% difference is not reliable: an earlier `/tts/stream` run in the 2-process case gave 1.05x. The likely cause of a real slowdown is shared GPU cores and shared memory bandwidth (decode is bandwidth-bound).

Note: Qwen3-TTS 1.7B bf16 is a little slower than real time on this Mac when it generates without pauses. This is a separate problem.

### Why 1 process stalls

- `POST /tts/stream` holds `inference_lock` for the full request (`backend/routes/tts_stream.py:163`, release in `_cleanup`).
- `WS /tts/ws` holds the lock for 1 segment (`backend/routes/tts_stream.py:336`). A segment can have up to `max_chunk_chars=400` characters, so 20 s of audio or more.
- `StreamSession` takes the same lock for each STT step (`backend/backends/stt_streaming.py:755`).

The lock is fair (FIFO), so each STT step waits for 1 full TTS segment. With LiveKit the LLM sends short sentences, so the wait is shorter than in this test, but it is still 1 full sentence of TTS (often 1-3 s). In 2 processes each process has its own lock. The remaining cost (+110 ms on the final) comes from GPU sharing.

### Memory (`footprint -p <pid>`, phys_footprint)

| Process | Idle | Steady after work | Peak |
|---|---|---|---|
| Server, no model | 292 MB | - | - |
| TTS (Qwen3-TTS 1.7B bf16) | - | 8.9 GB | 21 GB |
| STT (`qwen-asr-0.6B`) | - | 1.5 GB | 4.3 GB |
| 1 process with both models | - | 9.8 GB | 23 GB |

`ps` RSS does not show MLX GPU memory. Use `footprint`. An extra process costs about 0.3 GB. The TTS peak (21 GB) is high; the MLX buffer cache probably keeps it. With an external LLM (for example 8B 4-bit, about 5 GB) the total must stay under about 40 GB, so the workers need an MLX cache limit.

### Startup cleanup problem reproduced

1. `POST /generate` on process A (17594). Row status: `generating`.
2. Start process B (17596) on the same data directory. Log: `Marked 1 stale generation(s) as failed`. Row: `failed`, error `Server was shut down during generation`.
3. A finishes. Row: `completed`, but the error text stays.

## Target architecture

| Process | Port | Owns | Serves |
|---|---|---|---|
| **Main** (`--role main`) | 17593 (unchanged since v0.7.0) | DB (read and write), files, profiles, settings, history, job queue, model downloads, events, startup cleanup, migrations | Every route the UI uses today. TTS and STT work goes to the workers. |
| **TTS worker** (`--role tts`) | 17494 | Nothing. Reads the DB and the profile files. Writes only the voice prompt cache (`cache/*.prompt`). | `/tts/ws`, `/tts/stream`, `/tts/warmup`, new `/tts/render`, model load/unload/status, `/health` |
| **STT worker** (`--role stt`) | 17495 | Nothing. Reads the DB (capture settings). | `/stt/ws`, `/transcribe`, model load/unload/status, `/health` |
| **All** (`--role all`, default) | any | Everything | Everything, in 1 process (today's behavior) |
| External LLM (optional) | any | Its own models | OpenAI API. Used by refine and personality through the custom endpoint setting, and by the voice agent. |

A worker refuses routes outside its list (HTTP 404). Then a wrong URL fails at once and does not write state.

### Request flows

| Flow | Path |
|---|---|
| UI generation (`POST /generate`) | UI → main: DB row + queue → main calls `POST /tts/render` on the TTS worker → main normalizes, applies effects, saves the file, updates the row |
| `/speak`, MCP speak | Same as UI generation: they use `run_generation` |
| `/tts/stream`, `/tts/ws` on main | Main forwards to the TTS worker, or returns 404 and clients use the TTS worker URL (decide in step 4) |
| Dictation (`/transcribe`, captures, retranscribe) | UI → main: saves the capture → main calls `POST /transcribe` on the STT worker → main saves the text, runs refine (if an LLM endpoint is set) |
| Voice agent | LiveKit → STT worker `/stt/ws` and TTS worker `/tts/ws` directly. LLM: external. |
| Model download | UI → main downloads into the shared HF cache. Workers only load from the cache. |
| Model load/unload/status | UI → main → the worker that owns the model type |

### New worker route: `POST /tts/render`

Main today calls `generate_chunked(tts_model, text, voice_prompt, ...)` in `run_generation` (`backend/services/generation.py:115`). `/generate`, `/speak` and MCP speak all use `run_generation`. In `--role main` this call goes to `/tts/render` instead. (`generate_audio_sync` at `:271` has no callers.)

- Input: `profile_id`, `text`, `language`, `engine`, `model_size`, `seed`, `instruct`, `max_chunk_chars`, `crossfade_ms`.
- The worker builds the voice prompt, runs `generate_chunked` with trim and runaway detection, and returns WAV (or PCM16 with a sample-rate header).
- Main keeps the steps that write state: status updates, normalize, effects, file save, DB update.
- Cancel: main closes the HTTP request. The worker stops when the client leaves (the same pattern as `/tts/stream`).

## Remove the built-in LLM

Keep refine and personality. They work only when a custom OpenAI-compatible endpoint is set.

Done 2026-11-01. What changed compared with the table below:

- `get_llm_backend()` raises `LLMNotConfiguredError` (a `ValueError`), so the routes that already map `ValueError` to 400 needed no change.
- Dictation: without an endpoint, `POST /captures` returns `auto_refine=false`, so the client keeps the raw transcript. The UI no longer uses the LLM as a readiness gate.
- `llm_model` is removed from the settings API. The DB column stays. The Qwen size fields (`model_size`) are removed from the refine and `/llm/generate` requests.
- Locale strings that changed meaning are deleted from the non-English files. Those files show the English text until someone translates it.
- The README and `docs/content` pages still describe the built-in LLM. They need a separate docs update.

| Area | Change |
|---|---|
| `backend/backends/qwen_llm_backend.py` | Delete. |
| `backend/backends/__init__.py` | Delete `LLM_ENGINES["qwen_llm"]`, `_get_qwen_llm_configs` (`:577`), `get_llm_model_configs` and the Qwen branch of `get_llm_backend`. `get_llm_backend()` returns the OpenAI-compatible backend, or raises "No LLM endpoint is set" when there is none. |
| `backend/routes/llm.py` | `/llm/generate` uses only the custom endpoint. Delete the size check and the download path. Return 400 when no endpoint is set. |
| Refine (`backend/services/captures.py`, `backend/routes/captures.py`, `backend/services/refinement.py`) | Run refine only when an endpoint is set. Else keep the raw transcript and do not fail the capture. |
| Personality (`backend/services/personality.py`, `/generate`, `/speak`, `/profiles/{id}/compose`, MCP `default_personality`) | Return 400 with a clear message when no endpoint is set. |
| Settings (`backend/database/models.py:200-201`) | Keep the `llm_model` and `auto_refine` columns (no migration, smaller upstream conflict). Ignore `llm_model`. |
| UI: `CapturesPage.tsx`, `CapturesTab.tsx`, `useSettings.ts`, `types.ts`, `useCaptureRecordingSession.ts` | Delete the LLM size selector. Disable the refine toggles with a hint "Set an LLM endpoint" when no endpoint is set. |
| UI: `ModelManagement.tsx` | Delete the Qwen3 LLM model rows. |
| UI: personality (`ProfileForm.tsx`, `FloatingGenerateBox.tsx`) | Disable Compose and the personality toggle with the same hint when no endpoint is set. |
| `backend/build_binary.py` | Delete the hidden imports for the LLM backend. |
| Tests | Update `test_refinement_*`, `test_personality_samples.py`, `test_openai_compat_backend.py`, `test_models_load_by_name.py`, `test_mlx_smoke.py`. |

User data impact (checked on a copy of the app data): 0 captures, no personality texts, the Qwen3 LLM model is not downloaded. Nothing is lost.

## Problems and how the design solves them

| Problem | Solution |
|---|---|
| Startup cleanup marks the live jobs of another process as failed (reproduced) | Only main (and `all`) runs the cleanup. Also clear `error` when a generation completes (`backend/services/history.py:166-174`). |
| Migrations and seeds race when 2 processes start together (`backend/database/migrations.py`, `backend/database/seed.py`) | Only main runs `init_db()` migrations and seeds. Workers start after main is healthy and only open the DB. |
| CUDA/ROCm auto-update in 2 processes writes the same temp file (`backend/app.py:346-347`) | Only main runs it. |
| Custom LLM endpoint is loaded 1 time at startup (`backend/app.py:323-331`) | Only main uses the LLM. Workers do not load it. |
| Cancel sent to the wrong process marks the row failed (`backend/services/task_queue.py:24-28`) | The queue is only in main. Main cancels by closing the worker request. |
| `/tasks/active`, download progress, speak events are per process | All are in main. The UI only talks to main. |
| `/models/status` and `/health` show only the local process | Main asks the workers and merges the result. |
| `/models/migrate` moves the HF cache | Main restarts the workers with the new `VOICEBOX_MODELS_DIR`. |
| Desktop app knows 1 URL (`app/src/stores/serverStore.ts`) | No change: the UI uses main only. |
| `speak_monitor.rs:51` and `/watchdog/disable` (`main.rs:1635`) call the app port (`SERVER_PORT`) | No change: main keeps `SERVER_PORT` (17593). |
| Watchdog: the `.keep-running` file is shared, and the first watchdog deletes it (`backend/server.py:226-233`) | Tauri stops all 3 processes itself. Workers get `--parent-pid` and do not use `.keep-running`. Check first that the watchdog starts at all: it uses `@app.on_event("startup")`, which FastAPI may ignore with `lifespan` (`backend/app.py:149-156`). |
| TTS peak memory 21 GB | Workers set `mx.set_cache_limit`: TTS 2048 MB, STT 512 MB (measured in step 6; TTS peak 24 GB to 11 GB). |
| STT stalls behind TTS in `--role all` | Optional: hold `inference_lock` per TTS chunk, not per segment. Helps users with 1 process. |

## Tauri process manager

Today Tauri starts 1 server on 17593 (`tauri/src-tauri/src/main.rs:124`, spawn at `:547`, `:587-630`).

1. Start main on 17593. Wait for `/health`.
2. Start the TTS worker on 17494 and the STT worker on 17495 with the same `--data-dir`, `VOICEBOX_MODELS_DIR` and `--parent-pid`.
3. Main gets the worker URLs from arguments (`--tts-url`, `--stt-url`).
4. On exit or restart, stop all 3 processes. Reuse of a healthy server already on the port (`main.rs:283-351`) must check all 3 ports.
5. A setting turns the workers off. Then main runs with `--role all` (today's behavior).

## Plan (in order)

1. **Remove the built-in LLM.** Done 2026-11-01. See "Remove the built-in LLM".
2. **Add `--role main|tts|stt|all`.** Route allow-list per role. Workers skip cleanup, migrations, seeds, GPU auto-update and LLM bootstrap. Clear `error` on completion. Unit tests per role.
   Done 2026-11-01. `backend/roles.py` holds the role (env `VOICEBOX_ROLE`, set by `--role` in `server.py` and `main.py`) and `RoleMiddleware`. A worker answers 404 for HTTP paths outside its list and closes other WebSockets with code 1008. Both workers also serve `/shutdown` and `/watchdog/disable`. `init_db()` in a worker only opens the DB. `_run_main_startup()` in `app.py` holds the state steps (queue, stale cleanup, LLM bootstrap, CUDA/ROCm update). `--role main` still serves every route and runs TTS and STT itself until steps 3 and 4. Tests: `backend/tests/test_roles.py`.
3. **STT worker and forwarding.** Main sends `/transcribe` and capture transcription to the STT worker. Main model status asks the worker.
   Done 2026-11-01. `--stt-url` (only with `--role main`) sets `VOICEBOX_STT_URL`. `transcribe_with_model` and `load_stt_model` in `backend/services/transcribe.py` call the worker through `backend/services/workers.py`, so `/transcribe`, captures, retranscribe and MCP transcribe all use it. `/models/status` takes the STT `loaded` flag from the worker, and STT unload goes to the worker. Main's `/stt/ws` stays local: the UI does not use it, and LiveKit talks to the worker. Change from the design: the worker downloads STT models (through its `/models/load`), so main shows "downloading" without byte progress. A worker that is down gives `STT worker at ... is not reachable` at once. Measured: main 389 MB, STT worker 1.7 GB with `qwen-asr-0.6B` loaded. Tests: `backend/tests/test_stt_worker_forwarding.py`.
4. **TTS worker and forwarding.** Add `/tts/render`. Main sends `run_generation` work to the TTS worker. Decide if main forwards `/tts/stream` and `/tts/ws`. Check the voice prompt cache writes (atomic file write).
   Done 2026-11-01. `synthesize()` in `backend/services/generation.py` holds the shared steps (load, voice prompt, trim, runaway retry, `generate_chunked`). `render_speech()` runs it locally, or on the worker through `POST /tts/render` when main has `--tts-url`. `run_generation` (so `/generate`, retry, regenerate, `/speak`, MCP speak), `/generate/stream` and `/v1/audio/speech` use it. `/tts/render` returns float32 PCM with `X-Sample-Rate` and holds `inference_lock`. When main cancels, the worker stops before the next chunk (a running chunk is not cut, so the lock is free only when the GPU is free). TTS status, load, download and unload go to the worker like STT. The voice prompt cache uses a temp file and `os.replace`. Decision: main does not forward `/tts/stream`, `/tts/ws` and `/tts/warmup`; the UI does not use them, and streaming clients use the worker URL. Measured: main 297 MB, TTS worker 3.0 GB with `qwen-tts-0.6B`; cancel reached the worker in about 1 s. Tests: `backend/tests/test_tts_worker_forwarding.py`.
5. **Tauri process manager.** Start, health check, stop and restart for 3 processes, and the on/off setting.
   Done 2026-11-01. `start_server` in `tauri/src-tauri/src/main.rs` starts main (with `--role main --tts-url http://127.0.0.1:17494 --stt-url http://127.0.0.1:17495` when the setting is on), then asks main's `/health` for its role (new `role` field). If the role is `main`, `start_workers` starts each worker that is not already healthy on its port, with the same exe, `--data-dir`, `VOICEBOX_MODELS_DIR` and `--parent-pid`, and waits up to 120 s for its `/health`. The role decides, not the setting, so a reused main gets its workers back. Worker output goes to the server log with a `[tts]` or `[stt]` prefix. `stop_server` (so also `restart_server` and a models dir change) kills the workers and sends `/shutdown` to the worker ports for workers left by an earlier session. App exit stops them too; a keep-running exit sends `/watchdog/disable` to all 3 ports. Setting: "Separate TTS and STT processes" in Settings > General (Tauri only, default on, applies at the next app start, like Network access). Off: main starts without `--role`, so `all`. Workers bind 127.0.0.1 also in Network access mode.
   Found and fixed 2 old watchdog bugs: (1) `@app.on_event("startup")` never fires with `lifespan`, so the `--parent-pid` watchdog never started; `server.py` now starts it before `uvicorn.run`. (2) `/watchdog/disable` set a global in a second copy of `backend.server` (the server runs as `__main__`), so the watchdog never saw it; the flag is now the env var `VOICEBOX_WATCHDOG_DISABLED`. Workers pass no data dir to the watchdog, so only main reads and deletes `.keep-running`. Live check: 3 servers with `--parent-pid` of a `sleep` process exit about 5 s after it is killed; after `/watchdog/disable` on 3 ports they stay up.
   Limits: a worker that crashes is not restarted until the next `start_server` (app start or restart); its exit is logged. A reused main has no recorded GPU exe, so its workers use the bundled sidecar (right on macOS). In dev mode the app does not start servers, so the process manager runs only in a built app; it was checked with `cargo check`, not in a built app.
6. **Memory limits.** Measure and set `mx.set_cache_limit` per worker.
   Done 2026-11-01. `apply_worker_mlx_cache_limit()` in `backend/backends/base.py` runs at worker startup on MLX (`_run_startup` in `app.py`) and logs `MLX cache limit: N MB`. Defaults: TTS 2048 MB, STT 512 MB (`WORKER_MLX_CACHE_LIMIT_MB`). `VOICEBOX_MLX_CACHE_LIMIT_MB` overrides both. Main and `--role all` keep the MLX default. Tests: `test_worker_mlx_cache_limit` in `backend/tests/test_roles.py`.
   Measured on the dev Mac (48 GB), 1 worker per run, restarted for each value. TTS: Qwen3-TTS 1.7B bf16, the same 530-character text with seed 1 (26.5 s of audio), after a short warm-up render. STT: `/transcribe` of a 106 s WAV after a warm-up. "After" is `phys_footprint` after the request; "Peak" is `phys_footprint_peak` (includes model load).

   | Worker | Cache limit | Time | After | Peak |
   |---|---|---|---|---|
   | TTS 1.7B | MLX default | 20.3 s | 16 GB | 24 GB |
   | TTS 1.7B | 0 | 57.3 s | 5.1 GB | 9.8 GB |
   | TTS 1.7B | 512 MB | 24.0 s | 5.3 GB | 10 GB |
   | TTS 1.7B | 1024 MB | 21.0 s | 5.3 GB | 11 GB |
   | TTS 1.7B | **2048 MB** | 19.9 s | 7.5 GB | 11 GB |
   | TTS 1.7B | 4096 MB | 19.9 s | 10 GB | 13 GB |
   | STT `qwen-asr-0.6B` | MLX default | 1.75 s | 2.1 GB | 4.3 GB |
   | STT `qwen-asr-0.6B` | 0 | 1.84 s | 1.8 GB | 3.9 GB |
   | STT `qwen-asr-0.6B` | **512 MB** | 1.73 s | 2.1 GB | 3.9 GB |
   | STT `qwen-asr-1.7B` | MLX default | 1.22 s | 3.4 GB | 5.6 GB |
   | STT `qwen-asr-1.7B` | 512 MB | 1.22 s | 3.4 GB | 5.3 GB |

   Why these values: for TTS, 2048 MB is the smallest limit with no slowdown and halves the peak (24 GB to 11 GB). Smaller limits are slower (512 MB +18 %, 0 almost 3x). For STT the limit changes little; 512 MB cuts about 0.3 GB of peak at the same speed. With both workers and main, the voice stack now peaks at about 15 GB instead of about 28 GB, which leaves room for an external LLM on a 48 GB Mac. The steady footprint after a render stays at about model + cache limit, so the cache is not fully returned after a generation.
7. **Verify.** Run `scripts/stt_tts_contention_test.py` and `integrations/livekit/scripts/e2e_session.py --stt-port 17495 --tts-port 17494`. Run a UI generation and a dictation through main.
   Done 2026-11-01 on commit `0a07bcd`. Setup as Tauri starts it: main `--role main --tts-url http://127.0.0.1:17494 --stt-url http://127.0.0.1:17495` on 17690, TTS worker on 17494, STT worker on 17495, 1 copy of the app data directory, all 3 with `--parent-pid` of 1 parent process. The workers logged their cache limits (2048 MB and 512 MB). The installed app on 17593 was not touched.

   Contention test (`--wav /tmp/vb-mp/speech24k.wav --profile <Men 1>`, STT `qwen-asr-0.6B`, 1 warm-up and 6 rounds), next to the 2026-10-31 2-process numbers:

   | TTS load | Final delay median | Final delay max | Interims (of 13) | Max interim gap | TTS real-time factor | 2026-10-31 median / max |
   |---|---|---|---|---|---|---|
   | none | 119 ms | 123 ms | 13 | 521 ms | - | 131 / 142 ms |
   | `/tts/ws` | 291 ms | 623 ms | 12-13 | 838 ms | 0.85x | 242 / 546 ms |
   | `/tts/stream` | 212 ms | 562 ms | 12-13 | 843 ms | 1.07x | 236 / 259 ms |

   The split keeps the result of the research: under TTS load the final arrives in about 0.2-0.3 s (1 process: 8 s or a timeout). The cache limit does not slow TTS (0.85x and 1.07x, as before).

   LiveKit `AgentSession` (`uv run --extra example python scripts/e2e_session.py --stt-port 17495 --tts-port 17494 --profile-id <Men 1> ../../backend/tests/fixtures/speech_pause_speech_16k.wav`): both FINAL transcripts are correct, the agent speech was interrupted 2 times, and the agent stopped 1302 ms after the barge-in audio started. Result: transcription PASS, barge-in PASS. The script needs the `example` extra (Silero VAD).

   Through main, with the same calls the UI sends: `POST /generate` (Men 1, 1.7B) completed and main saved a 4.4 s WAV; `POST /captures` with a 6.7 s WAV returned the correct transcript (the saved STT model `whisper-turbo`, on the STT worker). Footprint after: main 388 MB, TTS worker 5.6 GB, STT worker 3.3 GB. `/models/status` on main shows `qwen-tts-1.7B` and `whisper-turbo` loaded (in the workers). Killing the parent process stopped all 3 servers.
8. Optional: hold `inference_lock` per TTS chunk for `--role all`.

## Rebase on upstream

The fork is rebased on upstream `main`. To keep conflicts low:

- `--role all` is the default, so upstream code paths stay the same.
- Put role and forwarding logic in new files (for example `backend/roles.py`, `backend/services/workers.py`). Touch existing files only at the call sites.
- The LLM removal is the largest conflict source (about 20 files). Keep DB columns, and keep the custom endpoint code unchanged.

## Not checked

- Other streaming STT models (Voxtral Realtime, Whisper over `/stt/ws`). Batch `whisper-turbo` ran in step 7.
- 3 processes with an external LLM that generates during TTS.
- The Tauri process manager in a built app (start, restart, models dir change, keep-running exit).
- A download through main of a model that is not on disk (it goes to the worker's `/models/load`, steps 3 and 4).
- Settings sync was checked by code reading only.

## Hardware (dev Mac)

| Item | Value |
|---|---|
| Model | MacBook Pro, Apple M4 Max |
| CPU | 16 cores (12 performance, 4 efficiency) |
| GPU | 1 GPU with 40 cores, Metal 4 |
| Memory | 48 GB unified. MLX recommended working set is about 40 GB. |
