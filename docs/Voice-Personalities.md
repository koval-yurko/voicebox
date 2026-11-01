_Attach a personality to a voice profile, compose fresh in-character lines, and rewrite input text in their voice — powered by an LLM endpoint you choose._

## Overview

A **personality** is an optional free-form description attached to a voice
profile — who this voice is, how they speak, what they care about. Set one
and two new controls appear next to the generate button. Both need an LLM
endpoint (see [The LLM endpoint](#the-llm-endpoint)):

- **Compose** — drop a fresh in-character line into the textarea. Click
  again for a different take.
- **Speak in character** — a toggle that rewrites your input text in the
  character's voice before TTS, preserving every idea.

The LLM produces the text. The voice profile speaks it. Run the LLM server on
the same machine and the whole loop stays on your hardware.

> [!NOTE]
> Personalities shipped in **0.5.0**. The same LLM endpoint also powers
> refinement for [Dictation](Dictation).

## Setting a personality

Open a voice profile's edit view. The **Personality** field is free-form text
up to **2,000 characters**. Describe the voice however helps you — past
lines they'd say, speech patterns, tone, boundaries.

Good descriptions tend to include:

- A one-line identity (who they are)
- Speech patterns (rhythm, vocabulary, what they avoid)
- Representative phrases — example lines show the LLM the target tone
  better than adjectives
- What the character *wouldn't* do (they don't explain, they don't
  apologize, they refuse to break character, etc.)

You can set a personality on any voice profile type — cloned or preset. The
three modes work identically regardless of engine.

## The two actions

Each action is tuned for a specific job and the LLM temperature is adjusted
to match.

### Compose

Generate a fresh utterance in the character's voice, with no seed text.
Click the shuffle button to drop a line straight into the generate
textarea; click again for a different take.

- **When to use:** prototyping, sampling a character's voice, brainstorming
  a line without typing one first
- **Temperature:** hot — variety is the point
- **Typical output:** a short, punchy line that fits the character's
  register

### Speak in character (rewrite)

Flip the persona toggle and whatever you type (or dictate) gets rewritten in
the character's voice before TTS — every idea preserved, only the phrasing
changes. High-fidelity mode: the content doesn't change, only the voice does.

- **When to use:** turning a dictated memo into in-character speech; lifting
  a plain-English script into a specific voice without editing by hand
- **Temperature:** cold — faithfulness wins
- **Typical output:** same ideas, same order, different phrasing and cadence

## Speech-only framing

Both modes enforce **speech-only** output. The LLM is prompted to
produce things a person would actually say out loud — no narration, no
action tags (`*sighs*`, `[laughs]`), no meta-commentary, no markdown
formatting, no stage directions.

This is deliberate: the output is going straight into TTS, and anything that
isn't speakable ends up either ignored or read literally. The speech-only
framing also makes the output land cleanly inside dialogue, so you can drop
a Respond result straight into a Story.

## The LLM endpoint

Voicebox has no built-in LLM. Personalities run on any OpenAI-compatible
`/v1/chat/completions` server — LM Studio, Ollama, `mlx_lm.server`,
llama.cpp, vLLM, or the OpenAI API. Set the URL, model and optional API key
in **Settings → Captures → Refinement → LLM endpoint**. Refinement uses the
same endpoint, so a model change applies to both on the next call.

Without an endpoint, Compose and the persona toggle are disabled, and the
API calls below return `400` with "No LLM endpoint is set".

## Using the controls

Both controls appear on the floating generate box when the selected profile
has a personality set.

**1. Compose — shuffle a line**

Click the shuffle button. The LLM runs and the result fills the generate
textarea. Edit if you want, then hit generate.

**2. Speak in character — toggle persona rewrite**

Type (or dictate) what you want said. Flip the wand toggle on. Hit
generate — Voicebox runs the text through the personality LLM first,
then TTS speaks the rewritten version. Leave the toggle off for plain
TTS.

Compose always gives you something different on re-click. The persona
toggle, on the other hand, is a mode — it applies to every generate call
until you flip it back off.

## Use cases

- **Agents that speak in a voice you own.** Combine the persona toggle with
  the built-in [MCP Server](MCP-Server) so Claude Code, Cursor,
  Cline, or any MCP-aware agent can talk back through a profile with a
  personality. The agent calls `voicebox_speak({ text, profile, personality:
  true })` and Voicebox rewrites the text in character before speaking.
- **Interactive characters.** Games, narrative tools, accessibility
  experiences. A character with a personality description plus a cloned
  voice becomes a reusable prop.
- **Accessibility.** People who can't speak in their original voice can
  keep a personality description of how they used to sound and use the
  rewrite toggle to turn typed input into in-character speech.
- **Creative drafting.** Write a plain outline, flip the persona toggle,
  generate line-by-line into the character's voice, drop the audio into a
  Story.

## API surface

Personalities are accessible via REST:

| Method | Endpoint | Body |
|---|---|---|
| `PUT` | `/profiles/{id}` | Include a `personality` field up to 2,000 chars to set it. |
| `POST` | `/profiles/{id}/compose` | No body. Returns a fresh in-character utterance as text. |
| `POST` | `/generate` | Include `personality: true` to run input text through the LLM endpoint before TTS. Same for `POST /speak`. |

`POST /generate` with `personality: true` is the same primitive MCP's
`voicebox_speak` tool uses when you pass `personality: true`. Scripts and
agents can use it directly.

## Limits and gotchas

- **The personality is a prompt, not a fine-tune.** The LLM will sometimes
  drift out of character, especially on Compose at high temperature. Click
  again for another take.
- **Long personalities are not always better.** 2,000 chars is a ceiling,
  not a goal. A sharp 300-char description with two example lines
  typically outperforms a long one.
- **Speech-only framing is enforced, but not bulletproof.** Very large
  prompts or unusual inputs can sneak an action tag through. If you see
  `[laughs]` in TTS output, it's usually a personality-field hint the
  model anchored onto — remove it from the description.
- **Rewrite is stricter than Respond.** If the output is changing your
  meaning, you probably want Respond (or a wholesale Compose with context
  in the input), not Rewrite.

## Next steps

- **[Dictation](Dictation)** — Dictate the input for Rewrite or Respond from anywhere on your machine.
- **[Captures](Captures)**

  Captures feed personalities naturally — dictate a memo, rewrite it in
  a character voice, generate speech.

- **[Creating Voice Profiles](Creating-Voice-Profiles)** — Add a personality to an existing profile.
