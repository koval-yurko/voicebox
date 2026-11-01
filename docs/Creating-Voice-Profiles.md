_How to create voice profiles, both cloning-based and preset-based_

## Overview

A **voice profile** is a saved voice you can reuse across generations, stories, and the API. As of 0.4, Voicebox profiles come in two flavors that map to two different ways of getting a voice:

| Profile type   | What it stores                                       | Use when…                                                |
| -------------- | ---------------------------------------------------- | -------------------------------------------------------- |
| **Cloned**     | One or more reference audio samples + a voice embedding | You want to replicate a specific person's voice          |
| **Preset**     | A reference to a pre-built voice in a specific engine | You want a curated, production-ready voice with no audio prep |

Both types live in the same Profiles tab and behave the same way at generation time — pick the type that matches your goal and follow the workflow below.

> [!NOTE]
> Not sure which to use? Cloning gives you a *specific* voice but needs clean audio. Preset gives you *good* voices instantly but you don't get to choose who they sound like.

## Workflow A — Cloned Profiles

Use this when you want to replicate a specific person's voice from a recording.

**1. Prepare Audio**

10-30 seconds of clear speech, minimal background noise. See [Voice Cloning](Voice-Cloning) for the engine catalog.

**2. Create Profile**

**Profiles** → **+ New Profile** → choose a cloning engine (Qwen3-TTS, Chatterbox Multilingual, Chatterbox Turbo, LuxTTS, or TADA)

**3. Upload or Record Sample**

Drag in an audio file, or record directly with the in-app recorder

**4. Generate to Test**

Use the profile to generate a test phrase. If quality is poor, add more samples

### Audio Requirements (Cloning Only)

- **Duration**

  **10-30 seconds**

  Too short: Poor quality
  Too long: Unnecessary

- **Clarity**

  **Clear speech**

  No background noise
  No music or overlapping voices

- **Quality**

  **High fidelity**

  44.1 kHz or 48 kHz sample rate
  Minimal compression

- **Content**

  **Natural speech**

  Conversational tone
  Complete sentences

### File Formats

Supported formats:
- **WAV** (recommended) — Lossless quality
- **MP3** — Acceptable, minimal compression
- **M4A** — Acceptable
- **FLAC** — Lossless alternative

> [!NOTE]
> Use WAV for best results. Avoid heavily compressed formats.

### Recording Tips

<details>
<summary>Quiet Space</summary>

- Record in a quiet room
- Turn off fans, AC, appliances
- Close windows to reduce outside noise
- Use soft furnishings to reduce echo

</details>

<details>
<summary>Microphone Placement</summary>

- 6-12 inches from mouth
- Slight angle to reduce plosives (p, b, t)
- Use a pop filter if available
- Maintain consistent distance

</details>

<details>
<summary>Recording Settings</summary>

- 44.1 kHz or 48 kHz sample rate
- 16-bit or 24-bit depth
- Mono is fine (stereo will be converted)
- Avoid automatic gain control

</details>

### Speaking Style

- **Natural pace** — Don't rush or speak too slowly
- **Clear articulation** — Pronounce words clearly
- **Consistent volume** — Maintain steady loudness
- **Normal tone** — Speak as you normally would
- **Complete sentences** — Avoid fragments or "ums"

### Multiple Samples

Adding multiple samples can significantly improve quality:

- **Robustness** — Model learns a more complete representation
- **Versatility** — Handles different speaking styles better
- **Quality** — Reduces artifacts and improves naturalness
- **Consistency** — More reliable across different texts

Consider adding samples with:

1. **Different tones** — casual, formal, excited, calm
2. **Different content** — narratives, questions, statements
3. **Different recording conditions** — studio quality, room acoustics

> [!WARNING]
> All samples should be from the **same speaker**. Mixing voices will produce poor results.

### Processing Existing Audio

If you have existing audio (podcasts, videos, etc.):

**1. Find Clean Speech**

Look for segments with just the target speaker, no background music, minimal noise

**2. Use Audio Editor**

Tools like Audacity or Adobe Audition: cut clean 10-30s segments, remove silence at start/end, normalize volume

**3. Export as WAV**

Save as high-quality WAV file

For light background noise, use Audacity's noise reduction (gentle settings — over-processing introduces artifacts).

### Testing & Iteration

After creating a cloned profile:

**1. Generate Test**

Try a simple phrase: `"Hello, this is a test of my voice profile."`

**2. Evaluate Quality**

Listen for natural tone, clear pronunciation, proper prosody, lack of artifacts

**3. Iterate**

If quality is poor: add more samples, try different source audio, check sample quality

#### Common Issues

<details>
<summary>Robotic Voice</summary>

**Cause**: Poor quality samples or too short

**Fix**: Use longer, higher-quality samples

</details>

<details>
<summary>Wrong Tone</summary>

**Cause**: Sample tone doesn't match desired output

**Fix**: Record samples in the style you want to generate

</details>

<details>
<summary>Artifacts/Glitches</summary>

**Cause**: Background noise or audio issues in samples

**Fix**: Clean up samples or re-record in quieter environment

</details>

## Workflow B — Preset Profiles

Use this when you want a ready-made voice without recording anything. Available engines: **Kokoro 82M** (50 voices) and **Qwen CustomVoice** (9 voices). See [Preset Voices](Preset-Voices) for the full catalog.

**1. Create Profile**

**Profiles** → **+ New Profile** → choose **Kokoro** or **Qwen CustomVoice** as the engine

**2. Pick a Voice**

The engine's voice catalog appears. Click any voice to preview it

**3. Name and Save**

Give the profile a name. No audio sample required

**4. Generate**

The profile is ready immediately — use it in the floating generate box or Generate page

> [!NOTE]
> Preset profiles are **locked to their source engine**. Switching to a different engine in the floating generate box greys out the profile, since the voice only exists in that engine. Clicking a greyed profile auto-switches the engine back.

### Qwen CustomVoice + Instruct

Preset voices in Qwen CustomVoice support **delivery instructions** — natural-language style control over tone, pace, and emotion. The floating generate box shows a slider icon next to the generate button when a Qwen CustomVoice profile is selected; click it to reveal the instruct textarea.

See [Preset Voices → Using Instruct Mode](Preset-Voices#using-instruct-mode) for examples.

## Advanced Tips

### Celebrity / Character Voices (Cloning)

For cloning public figures or characters:

1. **Legal considerations** — Ensure you have rights or it's clearly fair use
2. **Source quality** — Find high-quality interview audio or clean clips
3. **Consistency** — Use clips where they speak similarly
4. **Multiple samples** — Very important for recognizable voices

### Accent & Dialect (Cloning)

Cloning models preserve accent and dialect:

- British English samples generate British English output
- Southern accent samples produce Southern accent output
- Regional pronunciations are maintained

### Emotion Transfer (Cloning)

The emotional tone of samples affects generation:

- Energetic samples → energetic output
- Calm samples → calm output
- Mix samples for a more versatile profile

For Qwen CustomVoice presets, use the **instruct** field instead of relying on sample emotion — that's exactly what it controls.

## Managing Profiles

### Organization

- **Descriptive names** — "John Smith - Professional Narrator"
- **Add descriptions** — Note recording conditions, use cases, or which preset voice
- **Language tags** — Mark the primary language
- **Archive unused** — Keep profile list manageable

### Export / Import

- **Export** profiles to share or backup
- **Import** from colleagues or teammates
- **Cloned profiles** export with their voice embeddings (not the original audio)
- **Preset profiles** export as engine + voice ID metadata only — the importer must have that engine's model installed

## Next Steps

- **[Voice Cloning](Voice-Cloning)** — Engine catalog and best practices for cloning
- **[Preset Voices](Preset-Voices)** — Full catalog of Kokoro and Qwen CustomVoice voices
- **[Generate Speech](Generating-Speech)** — Use your profile to generate speech
- **[Build Stories](Building-Stories)** — Create multi-voice narratives
