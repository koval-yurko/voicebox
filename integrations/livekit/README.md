# Voicebox plugins for LiveKit Agents

Local STT and TTS for [LiveKit Agents](https://docs.livekit.io/agents/) 1.8+, through a Voicebox server on the same Mac:

- `VoiceboxSTT`: streaming STT over `WS /stt/ws` (protocol: `docs/research/stt-streaming-design.md` sections 7 and 8).
- `VoiceboxTTS`: streaming TTS over `WS /tts/ws`.

The agent connects over loopback (`127.0.0.1`), so the API key gate for external `/tts/*` requests does not apply.

## Setup

```bash
cd integrations/livekit
uv sync --extra example      # plugins + Silero VAD + OpenAI plugin (for Ollama)
```

The Voicebox server must have the STT model downloaded (`qwen-asr-0.6B` by default) and at least 1 voice profile.

## Usage

```python
from livekit.agents import AgentSession
from livekit.plugins import silero
from voicebox_livekit import VoiceboxSTT, VoiceboxTTS

session = AgentSession(
    stt=VoiceboxSTT(model="qwen-asr-0.6B"),
    tts=VoiceboxTTS(profile_id="<voice profile id>"),
    vad=silero.VAD.load(),
    llm=...,
)
```

`VoiceboxSTT` options:

| Option | Default | Meaning |
|---|---|---|
| `model` | `qwen-asr-0.6B` | Voicebox STT model. `None` uses the default model of the Voicebox capture settings. |
| `language` | `None` | ISO code. `None` lets the model detect the language. |
| `base_url` | `http://127.0.0.1:17493` | Voicebox server. |
| `vad` | `True` | `True`: the server VAD opens and closes segments. `False`: a segment closes only when the agent flushes the stream. |
| `interim` | `True` | Ask for interim text. `capabilities.interim_results` follows the server `ready` capabilities. |

Event mapping: `speech_start` → `START_OF_SPEECH`, `interim` → `INTERIM_TRANSCRIPT`, `final` → `FINAL_TRANSCRIPT`, `speech_end` → `END_OF_SPEECH`.

The plugin sets `manual_flush=True`. When the agent VAD detects the end of speech, the session flushes the stream, and the plugin sends `end_segment`. The server then closes the segment at once and does not wait for its own silence timeout.

Barge-in: the session closes the TTS stream when the user interrupts. The plugin then closes the `/tts/ws` socket, and the server stops the generation.

Note: by default `AgentSession` sends silence to the STT and ignores barge-in for the first 3 s of each agent reply (`aec_warmup_duration=3.0`), to prevent interruptions by echo. Set `aec_warmup_duration=None` when the audio path has no echo, for example with headphones.

## Example agent

```bash
uv run python examples/voice_agent.py console    # local mic and speaker, no LiveKit server
uv run python examples/voice_agent.py dev        # join rooms of LIVEKIT_URL
```

Without `OLLAMA_MODEL`, the agent repeats what it heard (echo mode, no LLM). The reply is long, so you can talk over it to test barge-in. With `OLLAMA_MODEL=<model>`, it answers through a local Ollama server. Other settings are in the docstring of `examples/voice_agent.py`.

## Checks

```bash
uv run pytest -q                                                  # plugin tests with fake servers
uv run python scripts/smoke_stt.py --port 17493 speech.wav        # VoiceboxSTT on a WAV file
uv run python scripts/e2e_session.py speech_pause_speech_16k.wav  # AgentSession: transcripts + barge-in
```

`e2e_session.py` runs a full `AgentSession` with a fake mic (a WAV file in real time) and a fake speaker, with the real Voicebox STT and TTS. It checks the final transcripts and that user speech during the agent reply stops the agent. Use `../../backend/tests/fixtures/speech_pause_speech_16k.wav`.
