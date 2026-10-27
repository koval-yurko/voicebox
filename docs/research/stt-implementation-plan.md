# STT implementation plan: multi-engine STT, Qwen3-ASR, streaming interface

Date: 2026-10-28. Base commit: `3abe9e1`.
Inputs: `docs/research/stt-investigation.md` (sections 7-8), `docs/research/stt-streaming-design.md`.
Target: Apple Silicon Mac, MLX, LiveKit agent on the same Mac (loopback).

## 0. Overview

| Phase | Goal | Depends on | Estimate |
|---|---|---|---|
| 0 | Spike: verify Qwen3-ASR quality and streaming cost before we build | — | 0.5-1 day |
| 1 | Multi-engine STT + Qwen3-ASR (batch) | — | 1-2 days + 0.5 day UI |
| 2 | Generic streaming interface + batch adapter + VAD + `WS /stt/ws` | 1 | 2-3 days |
| 3 | LiveKit streaming plugin | 2 | 1 day |
| 4 | Qwen3-ASR rolling streaming adapter (interim text) | 0, 2 | 2-3 days |
| 5 | Optional: Voxtral Realtime native adapter | 2 | 2-3 days |

Phase 0 and phase 1 can run at the same time. Do each phase as its own card and its own PR.

## 1. Key decisions

1. **1 STT model ID format.** Use the `ModelConfig.model_name` everywhere: `whisper-base` … `whisper-turbo`, `qwen-asr-0.6B`, `qwen-asr-1.7B`.
   The old Whisper size values (`base`, `small`, `medium`, `large`, `turbo`) stay valid as aliases. One function resolves both forms.
2. **No database schema change.** `capture_settings.stt_model` (`backend/database/models.py:198`) and `captures.stt_model` (`:278`) are already `String`. Old rows keep `"turbo"`, which the alias resolves. New rows store the full ID.
3. **1 STT model in memory at a time** (same as today). When a model of another engine loads, unload the current STT model first.
4. **Do not change `mlx-audio`.** Keep `mlx-audio==0.4.1` pinned. Streaming adapters use its internals, so a test must compare their output with batch output.
5. **The batch API stays.** `POST /transcribe`, captures and MCP keep working as today. Streaming is a new route.

## Phase 0 — Spike (de-risk)

Scripts in `scripts/spikes/` (not shipped, not committed). Run them with `backend/venv/bin/python`.

1. `qwen_asr_batch.py`: load `Qwen/Qwen3-ASR-0.6B` (and 1.7B) with `mlx_audio.stt.load`. Transcribe 3 clips (5 s, 30 s, 3 min, at least 1 non-English). Log the load time, real-time factor, text and peak memory. Do the same with `whisper-large-v3-turbo`.
2. Find the correct Hugging Face repo IDs (official and `mlx-community` variants). Decide which repo to register.
3. `qwen_asr_rolling.py`: prototype of the rolling re-decode (design doc section 5.3). Feed a WAV at real-time speed in 100 ms frames. Log each interim, final and step time.

Exit criteria:

- [ ] Qwen3-ASR 0.6B text quality is equal to or better than Whisper turbo on the clips (manual check).
  **Partly.** Equal or better on English, worse on German and on long Russian audio. Qwen3-ASR 1.7B is close to Whisper turbo on German and Russian, and Whisper turbo had 2 hallucinations that Qwen did not have. See 0.2.
- [x] Repo IDs and download sizes are known. See 0.1.
- [x] Rolling step time is below the 500 ms interval on this Mac. p95 = 91 ms (0.6B), 147 ms (1.7B); with Russian included, at most 204 ms. See 0.3.
- [x] Final rolling text equals batch text for at least 95 % of segments. 100 % (79/79, and 108/108 with Russian), on the condition that `finalize()` runs the batch path on the segment audio. With the prefix cache, 73/79 (92 %). See 0.3.

If the rolling check fails, phase 4 changes to Voxtral Realtime (phase 5) or to final-only streaming.
Result: the rolling check passes. Phase 4 stays as planned, with the changes in 0.4.

### Phase 0 results (2026-10-28)

Machine: Apple M4 Max, 48 GB. `mlx==0.32.4`, `mlx-audio==0.4.1`, `mlx-lm==0.31.1`.
Scripts: `scripts/spikes/fetch_clips.py`, `qwen_asr_batch.py`, `qwen_asr_rolling.py`. The scripts and raw results are throwaway and are not committed. They stay in the phase 0 card worktree (branch `kanban/phase-0-spike-qwen3-asr-quality-and-stre-fe9d54`). This section and 0.4 hold everything that later phases need.

Clips (16 kHz mono, with reference text; utterances joined with 0.8 s of low noise):

| Clip | Source | Length | Utterances |
|---|---|---|---|
| `en_5s` | LibriSpeech dev-clean (`hf-internal-testing/librispeech_asr_dummy`) | 5.0 s | 1 |
| `de_30s` | Multilingual LibriSpeech German test (`facebook/multilingual_librispeech`) | 33.1 s | 2 |
| `en_3min` | LibriSpeech dev-clean | 180.0 s | 17 |
| `de_3min` | Multilingual LibriSpeech German test | 189.5 s | 12 |
| `ru_30s` | Common Voice 17 Russian test (`fixie-ai/common_voice_17_0`), 4 speakers, consumer microphones | 31.8 s | 4 |
| `ru_3min` | Russian LibriSpeech test (`bond005/rulibrispeech`), Pushkin verse ("Ruslan and Lyudmila") | 185.8 s | 25 |

#### 0.1 Repo IDs and download sizes

| Repo | Download | Notes |
|---|---|---|
| `Qwen/Qwen3-ASR-0.6B` | 1.88 GB | Official, bf16. Loads with `mlx_audio.stt.load` (weights converted at load time). |
| `Qwen/Qwen3-ASR-1.7B` | 4.70 GB | Official, bf16, 2 shards. |
| `mlx-community/Qwen3-ASR-0.6B-8bit` | 1.01 GB | Text decoder 8-bit, audio encoder not quantized. |
| `mlx-community/Qwen3-ASR-1.7B-8bit` | 2.47 GB | Same. |
| `mlx-community/Qwen3-ASR-0.6B-bf16` / `-4bit` | 1.57 / 0.71 GB | Not tested. |
| `mlx-community/Qwen3-ASR-1.7B-bf16` / `-6bit` / `-4bit` | 4.08 / 2.04 / 1.61 GB | Not tested. |
| `openai/whisper-large-v3-turbo` (current Voicebox repo) | 1.62 GB | Reference. |

The `Qwen/Qwen3-ASR-*-hf` repos are for `transformers`. Do not use them with `mlx-audio`.

**Decision (proposal):** register `qwen-asr-0.6B` → `mlx-community/Qwen3-ASR-0.6B-8bit` and `qwen-asr-1.7B` → `mlx-community/Qwen3-ASR-1.7B-8bit`.
On these clips the 8-bit models have about the same WER as bf16 (within 1.7 points, not always worse). They are 20-30 % faster, and they use about 0.5 GB (0.6B) or 1.5 GB (1.7B) less memory.
If we want only official repos, use `Qwen/Qwen3-ASR-0.6B` / `-1.7B`. They need no other code.

#### 0.2 Batch quality and speed

`qwen_asr_batch.py`. Each model runs in its own process. The language is given (`English` / `German` / `Russian` for Qwen, `en` / `de` / `ru` for Whisper).
WER: lowercase, without punctuation, "Mr" counted as "mister", "ё" counted as "е". RTF = processing time / audio length (lower is faster). Peak = MLX peak memory, weights included.
The table is from the second run (with Russian). `results/batch.json` holds the texts.

| Model | Load | Weights | en_5s | de_30s | en_3min | de_3min | ru_30s | ru_3min | RTF (3 min clips) | Peak (3 min) |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3-ASR 0.6B (official) | 3.0 s | 1.46 GB | 0 % | 10.2 % | 3.3 % | 23.5 % | 4.3 % | 24.0 % | 0.017-0.028 | 3.7 GB |
| Qwen3-ASR 0.6B 8-bit | 2.8 s | 0.94 GB | 0 % | 8.5 % | 3.3 % | 23.2 % | 4.3 % | 23.1 % | 0.013-0.021 | 3.2 GB |
| Qwen3-ASR 1.7B (official) | 4.0 s | 3.80 GB | 0 % | 1.7 % | 2.3 % | 12.3 % | 4.3 % | 15.9 % | 0.034-0.057 | 6.1 GB |
| Qwen3-ASR 1.7B 8-bit | 3.2 s | 2.29 GB | 0 % | 1.7 % | 2.3 % | 12.9 % | 4.3 % | 15.9 % | 0.024-0.039 | 4.5 GB |
| Whisper large-v3-turbo | 3.1 s | 1.50 GB | 0 % | 5.1 % | 2.6 % * | 11.9 % | 0 % | 16.4 % ** | 0.016-0.046 | 2.3 GB |

\* In the first run Whisper turbo had 18.5 % on `en_3min`: it inserted 62 invented words in 1 place ("people call any birds with magic eyes …"). In the second run this did not occur. The hallucination is not stable from run to run.
\*\* Whisper turbo went into a loop on `ru_3min`: it wrote "безмолвные" 25 times in place of a part of the text.

Manual check:

- **English.** Qwen 0.6B and 1.7B make only small errors (names such as "Linnell's" → "Linel's"). Whisper turbo is equally good when it does not hallucinate.
- **German.** Qwen 0.6B makes many word errors on literary German ("Fixsternwelt" → "fikt sterne welt", "beherzte" → "beherrschte"). It is clearly worse than Whisper turbo. Qwen 1.7B is equal to Whisper turbo on `de_3min` and better on `de_30s`. Whisper drops some words.
- **Russian, short read sentences (`ru_30s`, Common Voice).** All Qwen models make 2 word-ending errors in 46 words ("надеялись" → "наделись", "потребностей" → "потребности"). Whisper turbo has no error.
- **Russian, verse (`ru_3min`).** All models have problems with archaic Pushkin verse. Qwen 0.6B splits and merges words ("ж вы" → "швы", "игривый" → "и гривой", "досугов" → "до сугов"). Qwen 1.7B makes fewer errors, about the same number as Whisper turbo ("лукоморья" → "лука море", "на дубе том" → "над дубетом"). Both write the modern form "золотая" for "златая". Whisper turbo also had the repetition loop.
- **Hallucinations.** Qwen had no inserted text and no loop in 2 runs (4 clips, then 6 clips). Whisper turbo had 1 hallucination in each run, both on 3-minute clips.
- All models: load time 3-4 s from the local cache. All are 17-80 times faster than real time.

**Language detection.** With `language=None`, Qwen3-ASR 0.6B reported German audio as `English`. On a 4 s German clip it then gave wrong text ("Duncan's, so" for "Denken Sie, so"). Russian (`ru_30s`) was detected correctly by all Qwen models. Voicebox must always pass the language name, and the default for an unknown language must be a setting, not auto-detect.

**Conclusion.** Qwen3-ASR 0.6B can replace Whisper turbo for English. For other languages, use Qwen3-ASR 1.7B (8-bit). Its quality is close to Whisper turbo (better on German `de_30s`, about the same on the 3-minute clips, a little worse on short Russian sentences), and it does not hallucinate. Whisper turbo stays the default until phase 1 ships (see phase 1, "Default model").

#### 0.3 Rolling re-decode prototype

`qwen_asr_rolling.py`. The WAV is fed in 100 ms frames at real-time speed. An energy VAD (−45 dBFS) ends a segment after 500 ms of silence (79 segments on the English and German clips, 108 with Russian; at most 13 s each). 1 interim step every 500 ms:

1. Encode only the open 8 s window. Closed windows are encoded once and prefilled into the decoder prefix KV cache.
2. Prefill the open-window audio + `<audio_end>` + assistant header + `language X<asr_text>` + committed text on top of the prefix cache. Greedy decode, at most 24 new tokens. `trim()` the cache back to the prefix.
3. LocalAgreement-2: commit the complete words that 2 steps in a row agree on.
4. At segment end: final text.

The self-check passes: the windowed mel is exactly equal to batch mode (diff 0.0), and the windowed encoder output differs by at most 5e-6.

| Model | Interim step p50 / p95 / max | Window close (max) | Final latency p50 / max | First interim after speech start | Skipped ticks | Final == batch | Peak |
|---|---|---|---|---|---|---|---|
| 0.6B (official, real time) | 70 / 91 / 130 ms | 54 ms | 101 / 249 ms | 0.56 s | 0 | 79/79 | 2.8 GB |
| 1.7B (official, real time) | 114 / 147 / 186 ms | 89 ms | 195 / 509 ms | 0.60 s (max 1.13 s) | 3 | 79/79 | 5.5 GB |
| 0.6B 8-bit (`--fast`, with Russian) | 51 / 74 / 124 ms | — | 111 / 304 ms | 0.54 s | 0 | 108/108 | 2.2 GB |
| 1.7B 8-bit (`--fast`, with Russian) | 94 / 155 / 204 ms | — | 192 / 579 ms | 0.57 s | 0 | 108/108 | 3.9 GB |

Russian only (`ru_30s` + `ru_3min`, 29 segments): 0.6B 8-bit step p95 84 ms, 1.7B 8-bit step p95 168 ms (max 204 ms). The final latency is higher for Russian (1.7B 8-bit: p50 335 ms, max 579 ms on `ru_3min`), because the same speech needs more text tokens. Final == batch: 29/29 for both models.

(`--fast` does not sleep. The step cost is the same, because the steps run on the same thread.)

Findings:

- **The step time is not a problem.** All steps are far below 500 ms. The interval can go down to 250 ms for faster interim text.
- **Final text:** if `finalize()` decodes on top of the prefix cache, only 73/79 (0.6B) and 78/79 (1.7B) segments equal batch. This is not a logic error. On hard words the top 2 tokens are almost equal in bf16 (example: logit margin 0.0 between "Weltschiff" and "Weltschiffe"). Any change in how the prompt is split into prefill calls can select the other token. If `finalize()` runs the batch path (`model.generate()` on the segment audio), the result is 79/79 by construction. It costs the same time, because the decode is most of the cost and encoder + prefill are small.
- **Stable interim text is not final.** For 0.6B, the last committed text was not a prefix of the final text in 25/79 segments (1.7B: 16/79). LocalAgreement-2 commits the first words after about 1 s of audio, and the full-context decode can change them later ("Mr" → "Mister", spelling of names). With LiveKit this is correct behavior, because FINAL replaces all interim text. But the adapter must not say that `stable_text` never changes inside a segment.

#### 0.4 Changes for phase 1 and phase 4

Phase 1:

- Use the repo IDs from 0.1 in `_get_qwen_asr_configs()`. `size_mb`: 1010 (0.6B 8-bit), 2470 (1.7B 8-bit).
- `to_qwen_language()` must always return a name. Do not pass `None` to Qwen3-ASR (see "Language detection" in 0.2).
- Default model and recommendation: see phase 1, "Default model". Whisper turbo stays the default. `qwen-asr-1.7B` (8-bit) is the recommended Qwen3-ASR model for languages other than English. `qwen-asr-0.6B` is for English only.

Phase 4 (changes to design doc 5.3):

1. `finalize()` = the batch path on the segment audio. Use the prefix cache only for interim steps.
2. Prefill like `mlx_lm.generate_step`: all prompt tokens except the last in 1 call, then the last token alone. In an isolated test this fixed 3 of the 5 differences on `de_3min`. In the full 0.6B run the incremental result went from 72/79 to 73/79.
3. The mel clamp uses the maximum of the complete segment (`max - 8.0` in `WhisperFeatureExtractor`). Use the maximum of final frames only (closed windows + current open window), not a running maximum, because the last open frame changes when audio arrives. A closed window must be encoded again if the segment maximum later grows by enough to change its clamp (2 cases in 79 segments).
4. Pad the last partial 1 s chunk of a window to 100 frames, as batch mode does. Without this, the last audio tokens differ.
5. Interim text from LocalAgreement-2 can change. Document `stable_text` as "stable within the current hypothesis", not "final".
6. Implement the window encoder as in batch mode, but for 1 window: split into 1 s chunks of 100 mel frames, run `audio_tower.conv2d1-3` + `conv_out` + positional embedding per chunk, keep the valid tokens, run the encoder layers on the window with no mask (1 block), then `ln_post`, `proj1`, `proj2`. In phase 0 this matched `get_audio_features()` within 5e-6. The equality test: final == `model.generate()` for each segment.

## Phase 1 — Multi-engine STT + Qwen3-ASR (batch)

### Default model (decided after phase 0)

- **Whisper turbo stays the default** until phase 1 ships. Do not change the default `stt_model` in capture settings, `TranscriptionRequest` or MCP. Existing users see no change. The Qwen3-ASR models are opt-in.
- **Recommended Qwen3-ASR model for languages other than English: `qwen-asr-1.7B` (`mlx-community/Qwen3-ASR-1.7B-8bit`).** In phase 0 it was close to Whisper turbo on German and Russian, and it had no hallucinations. `qwen-asr-0.6B` was clearly worse on German and on Russian verse (WER 23-24 %).
- **`qwen-asr-0.6B` (`mlx-community/Qwen3-ASR-0.6B-8bit`) is for English.** It is the fastest model and as good as 1.7B on English.
- After phase 1 ships, review the default again with real captures. A candidate rule: `qwen-asr-0.6B` for English, `qwen-asr-1.7B` for other languages, Whisper turbo for languages that Qwen3-ASR does not support (for example Ukrainian; see `config.support_languages`, 30 languages).

### Step 1.1 — STT engine registry

File: `backend/backends/__init__.py`

- Add `STT_ENGINES = {"whisper": "Whisper", "qwen_asr": "Qwen3-ASR"}`.
- Replace the single `_stt_backend` (line 204) with `_stt_backends: dict[str, STTBackend]` and a lock, like `_tts_backends` (line 202).
- `get_stt_backend(engine: str = "whisper")` returns or creates the instance for that engine. Keep the no-argument call working (Whisper), so current callers do not break.
- Add `get_stt_model_configs()` that returns `_get_whisper_configs() + _get_qwen_asr_configs()`.
- Add `resolve_stt_model(name: str | None) -> ModelConfig`:
  - `None` → the capture settings default.
  - `"turbo"` and the other sizes → `whisper-<size>`.
  - a full model name → its config.
  - unknown → `ValueError` (the routes return HTTP 400).
- Add `unload_other_stt_engines(keep: str)` for decision 3.
- Update `reset_backends()` (line 775).

### Step 1.2 — Model entries

File: `backend/backends/__init__.py`

- `_get_qwen_asr_configs()`: `qwen-asr-0.6B` → `mlx-community/Qwen3-ASR-0.6B-8bit` (`size_mb=1010`) and `qwen-asr-1.7B` → `mlx-community/Qwen3-ASR-1.7B-8bit` (`size_mb=2470`), `engine="qwen_asr"`. `languages`: the full list from `config.support_languages` for both. Show "English" as the recommended use of 0.6B in its description only, so that users can still select it for other languages.
- Add them wherever the STT configs are listed, so the model screen shows download, status and unload.
- Update the 3 places that check only `engine == "whisper"` (around lines 557, 600 and 630): status, unload and load. Change each check to `engine in STT_ENGINES` and call the engine backend.
- Check `backend/routes/models.py` (`/models/status`, `/models/load`, `/models/download`) for more Whisper-only branches.

### Step 1.3 — Qwen3-ASR backend

New file: `backend/backends/qwen_asr_backend.py`

- `MLXQwenASRBackend`, with the same structure as `MLXSTTBackend` (`backend/backends/mlx_backend.py:371`):
  - `_is_model_cached(model_size)`, `load_model_async` / `load_model` in `asyncio.to_thread`, inside `model_load_progress(...)`.
  - `transcribe(audio_path, language, model_size) -> str`: calls `self.model.generate(path, language=<name>)` and returns `result.text.strip()`.
  - `unload_model()`, `is_loaded()`, `model_size` attribute.
- Language conversion helper `to_qwen_language(code)`: `en` → `English`, through `LANGUAGE_CODE_TO_NAME` (`backend/backends/__init__.py:26`) and `model.config.support_languages`. Always return a name. `None` or `auto` → the default language from the settings (English if none is set), not `None`: phase 0 showed that auto-detection is not reliable (German detected as English).
- Optional: return the detected language. This needs a richer return type, so keep it for phase 2 events.

### Step 1.4 — Model choice in the API

| File | Change |
|---|---|
| `backend/models.py:175` `TranscriptionRequest.model` | Remove the size regex. Validate with `resolve_stt_model`. |
| `backend/models.py:251`, `:275` capture settings `stt_model` | Same. Accept old sizes and full IDs. |
| `backend/routes/transcription.py:19-80` | Replace `WHISPER_HF_REPOS` validation with `resolve_stt_model`. Select the backend by `config.engine`. Keep the HTTP 202 download behavior for all engines. |
| `backend/services/transcribe.py` | `get_stt_for_model(name) -> (backend, config)`. Keep `get_whisper_model()` as a wrapper for old callers. |
| `backend/services/captures.py:120-131` | Use `get_stt_for_model`. Store the full model ID in `stt_model`. |
| `backend/routes/captures.py` (create, retranscribe, readiness at line 157) | Resolve the model the same way. Readiness checks the chosen engine. |
| `backend/mcp_server/tools.py:284-311` `_transcribe_file` | Same. Update the tool description (model names). |
| `backend/routes/settings.py` | Return the stored value. The UI resolves aliases. |

After the change, run `just generate-api` (`scripts/generate-api.sh`) to update the app API client.

### Step 1.5 — Settings and UI

| File | Change |
|---|---|
| `app/src/components/ServerTab/CapturesPage.tsx:131`, `:339` | STT model picker: add Qwen3-ASR 0.6B / 1.7B. Use full IDs for new choices. Show an old `"turbo"` value as Whisper Turbo. Keep Whisper Turbo selected by default. Labels: "Qwen3-ASR 0.6B — English, fastest" and "Qwen3-ASR 1.7B — recommended for other languages". |
| `app/src/components/CapturesTab/CapturesTab.tsx:163` | Same default handling. |
| `app/src/components/ServerSettings/ModelManagement.tsx:76-84` | Add descriptions for `qwen-asr-0.6B` (English, 1.0 GB) and `qwen-asr-1.7B` (recommended for languages other than English, 2.5 GB). |
| i18n files (`settings.captures.transcription.model.*`) | Labels and short descriptions. |

### Step 1.6 — Tests

New file: `backend/tests/test_stt_registry.py` (runs with no model download):

- `resolve_stt_model`: `"turbo"` → `whisper-turbo`; `"qwen-asr-0.6B"` → `engine == "qwen_asr"`; `None` → default; `"bogus"` → `ValueError`.
- `to_qwen_language`: `en` → `English`, `zh` → `Chinese`, `ru` → `Russian`, `None` / `auto` → the default language name, unknown or unsupported code (for example `uk`) → the default language name, with a logged warning.
- `get_stt_backend("qwen_asr")` returns the same instance twice. Loading a Qwen model unloads Whisper (use a fake backend).
- Pydantic models accept old sizes and full IDs, and reject unknown values.

Extend `backend/tests/test_all_models_e2e.py` (or add `test_qwen_asr_download.py` like `test_whisper_download.py`): download, load and transcribe a fixture clip with `qwen-asr-0.6B`. Mark the test as slow.

Manual check: transcribe the same clip with `whisper-turbo` and `qwen-asr-0.6B` through `POST /transcribe` and through a capture. Compare the text and the time.

Commands:

- `just test` (`pytest backend/tests -v`)
- `backend/venv/bin/ruff check backend`
- `bun run typecheck` and `bun run lint` (root `package.json`)

### Phase 1 acceptance criteria

- [ ] `POST /transcribe` with `model=qwen-asr-0.6B` returns text. With `model=turbo` it works as before.
- [ ] Captures and retranscribe work with both engines. Old captures with `stt_model="turbo"` still show and retranscribe.
- [ ] MCP `voicebox.transcribe` accepts both model forms.
- [ ] The model screen downloads, shows status for and unloads both Qwen3-ASR models.
- [ ] Only 1 STT model is in memory after an engine switch.
- [ ] The default STT model is still Whisper turbo for new and existing installs.
- [ ] All new unit tests pass. The existing test suite still passes.

## Phase 2 — Generic streaming interface

Full design: `docs/research/stt-streaming-design.md` sections 3-7.

### Steps

1. **Interface:** new file `backend/backends/stt_streaming.py` with `STTEventType`, `STTEvent`, `STTStreamCapabilities`, `STTStreamOptions`, `STTStream`, `StreamingSTTBackend`.
2. **Registry:** add `stream_adapter: str = "segmented"` to `ModelConfig` (`backend/backends/__init__.py:49`). Add `open_stt_stream(options) -> STTStream`, which resolves the model and selects the adapter.
3. **`StreamSession`:** PCM16 → float32, resampling to 16 kHz with `librosa`, a 1-thread executor for each model, the event queue, and skipping interim ticks when the model is busy.
4. **`SegmentedBatchStream`:** wraps any `STTBackend`. It buffers the segment, calls `transcribe()` on `end_segment()`, and emits FINAL. It writes a temp WAV for now, because `transcribe()` takes a path. Later, add `transcribe_array()` to the protocol.
5. **Segmenter (VAD):** server-side Silero VAD. Add the `silero-vad` dependency (or `onnxruntime` + the model file) to `backend/requirements.txt` and to the PyInstaller spec (`backend/voicebox-server.spec`, `backend/build_binary.py`). Settings: start after 200 ms speech, end after 500 ms silence, 300 ms pre-roll. `vad: false` turns it off.
6. **Locking:** each model step holds `inference_lock` (`backend/services/task_queue.py:32`) only for that step.
7. **Route:** new file `backend/routes/stt_stream.py` with `WS /stt/ws` (protocol in design doc section 7). Register it in `backend/routes/__init__.py`. Handle client disconnect, as in `backend/routes/tts_stream.py`.
8. **External access:** none is needed. The LiveKit agent uses loopback, and the API key gate (`backend/utils/api_keys.py`) does not apply to it. Add `/stt/` to `EXTERNAL_PATH_PREFIXES` only if remote clients need it later.

### Tests

- `backend/tests/test_stt_streaming.py`:
  - `SegmentedBatchStream` with a fake backend: push frames, call `end_segment()`, get exactly 1 FINAL with the fake text.
  - `cancel_segment()` gives no FINAL.
  - Resampling: 48 kHz input gives the expected 16 kHz sample count.
  - Skipping interim ticks: a slow fake model receives all the audio and gets fewer interim calls.
- `backend/tests/test_stt_ws.py` (FastAPI `TestClient` WebSocket): `start` → `ready`, binary frames, `end_segment` → `final`, `end` → `end`. Bad `start` → `error`.
- VAD: a fixture with speech, silence and speech gives 2 segments.

### Phase 2 acceptance criteria

- [ ] `WS /stt/ws` streams FINAL text for every registered STT model (Whisper, Qwen3-ASR).
- [ ] The server VAD emits `speech_start` / `speech_end` and closes segments by itself.
- [ ] TTS streaming still works while STT streams (manual check: first TTS chunk latency does not double).
- [ ] The batch routes do not change their behavior.

## Phase 3 — LiveKit plugin

- Package: `integrations/livekit/voicebox_stt.py` (or a separate repo, as the user prefers).
- `VoiceboxSTT(stt.STT)`: `streaming=True`, `interim_results` from the `ready` capabilities.
- `RecognizeStream._run()`: open `ws://127.0.0.1:<port>/stt/ws`, resample with `rtc.AudioResampler` to 16 kHz, send binary frames, and map events (design doc section 8). On `FlushSentinel`, send `end_segment`.
- Example agent: `AgentSession(stt=VoiceboxSTT(model="qwen-asr-0.6B"), tts=<Voicebox TTS>, vad=silero.VAD.load(), ...)`.

Acceptance:

- [ ] A local LiveKit agent session transcribes speech through Voicebox, with final transcripts in the agent log.
- [ ] Barge-in works: user speech during TTS stops the agent speech.

## Phase 4 — Qwen3-ASR rolling streaming adapter

- `RollingRedecodeStream` base class (design doc section 5.2) in `backend/backends/stt_streaming.py`.
- `QwenASRRollingStream` in `backend/backends/qwen_asr_streaming.py` (design doc section 5.3): closed-window encoder cache, decoder prefix KV cache with `trim()`, the open window encoded again for each step, LocalAgreement-2 stability, and `finalize()` = full decode.
- Set `stream_adapter="rolling"` on the Qwen3-ASR configs.
- Tests: an equality test (rolling FINAL == batch text on fixture clips), a step-time log, and an interim stability check (`stable_text` never gets shorter).

Acceptance:

- [ ] Interim text arrives less than 1.2 s after the speech starts.
- [ ] FINAL text equals batch text on the test clips.
- [ ] The LiveKit plugin shows interim transcripts.

## Phase 5 — Optional: Voxtral Realtime

Only if phase 4 is too slow or too unstable. Design doc section 5.4.

## 2. Next steps

1. ~~Run phase 0~~ (spike). Done on 2026-10-28; see "Phase 0 results". Phase 4 is possible, with the changes in 0.4.
2. **Start phase 1** on a new branch, with the repo IDs from 0.1 and the "Default model" rules.
3. Create 1 card for each phase (1-4), with the acceptance criteria above.
4. Optional: a card to stop Whisper turbo hallucinations on long audio (found in phase 0, see 0.2).

Note: the earlier idea "Multi-engine STT registry with faster-whisper and Parakeet v3" is replaced by phase 1 of this plan. faster-whisper does not use the Mac GPU.
