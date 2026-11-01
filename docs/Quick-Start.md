_Get started with Voicebox in 5 minutes_

This guide will walk you through creating your first voice profile and generating speech.

## Prerequisites

Make sure you have [installed Voicebox](Installation) and launched the app.

## Step 1: Create a Voice Profile

Voice profiles are the foundation of Voicebox. Each profile contains voice samples that the AI uses to clone the voice.

**1. Navigate to Voices**

Click the **Voices** tab in the sidebar

**2. Create New Voice**

Click the **+ New Voice** button

Fill in the details:
- **Name:** A descriptive name (e.g., "John Smith")
- **Language:** Select the primary language
- **Description:** Optional notes about the voice

**3. Add Voice Sample**

You have two options:

**Option A: Upload Audio**
- Click **Upload Sample**
- Select an audio file (WAV, MP3, or M4A)
- Ideal length: 10-30 seconds of clear speech

**Option B: Record Live**
- Click **Record Sample**
- Speak clearly for 10-30 seconds
- Click stop when finished

**4. Save Profile**

Click **Create Profile** to save

> [!NOTE]
> For best results, use clean audio with minimal background noise and consistent speaking tone.

## Step 2: Generate Speech

Now let's use your new voice profile to generate speech.

**1. Go to Generation**

Click the **Generate** tab in the sidebar

**2. Select Voice Profile**

Choose your newly created profile from the dropdown

**3. Enter Text**

Type or paste the text you want to generate:

```
Hello! This is my first voice generation with Voicebox.
```

> [!NOTE]
> Paralinguistic tags like `[laugh]`, `[sigh]`, and `[gasp]` only work with
> **Chatterbox Turbo**. Qwen3-TTS, LuxTTS, Chatterbox Multilingual, and
> HumeAI TADA will read those tags literally instead of turning them into
> expressive sounds.

To insert supported tags, select **Chatterbox Turbo** and type `/` in the
text input to open the tag inserter.

**4. Generate**

Click **Generate** and wait a few seconds

> [!NOTE]
> First generation may take longer due to model initialization. Subsequent generations will be faster.

**5. Play & Download**

- Click **Play** to preview the audio
- Click **Download** to save the audio file
- The generation is also saved to your **History**

## Step 3: Build a Story (Optional)

The Stories Editor lets you create multi-voice narratives with a timeline-based interface.

**1. Create New Story**

Navigate to **Stories** and click **+ New Story**

**2. Add Voice Tracks**

Click **+ Add Track** to create tracks for different speakers

**3. Add Audio Clips**

- Drag generated audio from your History
- Or generate new clips directly in the timeline
- Arrange clips on the timeline

**4. Edit & Export**

- Trim clips by dragging edges
- Adjust timing and spacing
- Click **Export** to render the final audio

## What's Next?

- **[Voice Cloning Guide](Creating-Voice-Profiles)** — Learn advanced techniques for high-quality voice cloning
- **[API Integration](API)** — Integrate Voicebox into your own applications
- **[Stories Editor](Stories-Editor)** — Master the multi-track timeline editor

## Tips for Success

<details>
<summary>Getting the Best Voice Quality</summary>

- Use 10-30 seconds of clear, consistent speech
- Avoid background noise and echo
- Multiple samples from the same speaker improve quality
- Match the speaking style you want to generate

</details>

<details>
<summary>Improving Generation Speed</summary>

- Use an engine with an MLX build, which runs on the Apple Silicon GPU
- Enable voice prompt caching for repeated generations

</details>

<details>
<summary>Troubleshooting Common Issues</summary>

- **Server won't start:** Check if port 17593 is available
- **Poor audio quality:** Try adding more voice samples
- **Slow generation:** Check that Settings → Server Status shows **Backend: MLX**
- See the full [Troubleshooting Guide](Troubleshooting) for more

</details>
