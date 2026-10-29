"""Local voice agent: Voicebox STT + Voicebox TTS + Silero VAD, all on this Mac.

    uv sync --extra example
    uv run python examples/voice_agent.py console      # local mic and speaker, no LiveKit server
    uv run python examples/voice_agent.py dev          # join rooms of LIVEKIT_URL

Environment (all optional):
    VOICEBOX_URL         Voicebox server for STT and TTS (default http://127.0.0.1:17493)
    VOICEBOX_STT_URL     STT server, if it differs from VOICEBOX_URL
    VOICEBOX_STT_MODEL   default qwen-asr-0.6B
    VOICEBOX_PROFILE_ID  TTS voice profile (default: the first profile of the server)
    OLLAMA_MODEL         answer with this local Ollama model; without it the agent
                         repeats what it heard (echo mode, no LLM needed)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, cli, llm
from livekit.plugins import openai, silero

from voicebox_livekit import VoiceboxSTT, VoiceboxTTS

load_dotenv()
logger = logging.getLogger("voicebox-agent")

VOICEBOX_URL = os.getenv("VOICEBOX_URL", "http://127.0.0.1:17493")
STT_URL = os.getenv("VOICEBOX_STT_URL", VOICEBOX_URL)
STT_MODEL = os.getenv("VOICEBOX_STT_MODEL", "qwen-asr-0.6B")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

# Long enough to talk over it: the barge-in test.
ECHO_TAIL = (
    " I will keep talking for a while now, so you can test barge-in."
    " Start to speak at any time and I will stop and listen to you."
)


def default_profile_id() -> str:
    if profile_id := os.getenv("VOICEBOX_PROFILE_ID"):
        return profile_id
    with urllib.request.urlopen(f"{VOICEBOX_URL}/profiles", timeout=10) as resp:
        profiles = json.load(resp)
    if not profiles:
        raise SystemExit(f"No voice profiles on {VOICEBOX_URL}. Create one in Voicebox first.")
    logger.info("TTS voice profile: %s (%s)", profiles[0]["name"], profiles[0]["id"])
    return profiles[0]["id"]


class EchoAgent(Agent):
    """Repeats each user turn. No LLM: the test covers only STT, TTS and turns."""

    def __init__(self) -> None:
        super().__init__(instructions="Repeat what the user said.")

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        # With llm=None the session gives no reply of its own; speak here instead.
        heard = (new_message.text_content or "nothing").rstrip(" .!?")
        self.session.say(f"You said: {heard}.{ECHO_TAIL}")


class LocalAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a helpful voice assistant. Answer in 1 to 3 short sentences. "
                "Do not use lists, markdown or emojis."
            )
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["profile_id"] = default_profile_id()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=VoiceboxSTT(model=STT_MODEL, base_url=STT_URL),
        tts=VoiceboxTTS(profile_id=ctx.proc.userdata["profile_id"], base_url=VOICEBOX_URL),
        vad=ctx.proc.userdata["vad"],
        llm=openai.LLM.with_ollama(model=OLLAMA_MODEL) if OLLAMA_MODEL else None,
    )

    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.is_final:
            logger.info("FINAL transcript: %s", ev.transcript)

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        logger.info("agent state: %s -> %s", ev.old_state, ev.new_state)

    await session.start(agent=LocalAssistant() if OLLAMA_MODEL else EchoAgent(), room=ctx.room)
    await session.say("Hello. I run fully local on this Mac. Say something.")


if __name__ == "__main__":
    cli.run_app(server)
