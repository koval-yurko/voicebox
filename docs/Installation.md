_Download and install Voicebox YK on macOS (Apple Silicon)_

## Download

Voicebox YK is built for macOS on Apple Silicon only.

Download the DMG from [the latest release](https://github.com/koval-yurko/voicebox/releases/latest).

## Install

1. Open the DMG.
2. Drag **Voicebox YK** to the **Applications** folder.
3. Remove the quarantine flag. The app is not signed or notarized by Apple, so macOS blocks it otherwise.

```bash
xattr -dr com.apple.quarantine "/Applications/Voicebox YK.app"
```

You can also right-click the app in Finder and choose **Open**.

Voicebox YK uses its own bundle id and port. It can run next to the original Voicebox app.

## First Launch

When you launch Voicebox YK for the first time:

1. **Model Download** — The TTS engine you generate with first will download its model automatically. Sizes range from ~350 MB (Kokoro) to ~8 GB (TADA 3B). Most users start with Qwen 1.7B (~3.5 GB).
2. **Data Directory** — Voice profiles and generated audio are stored in `~/Library/Application Support/com.koval-yurko.voicebox-yk/`.
3. **Backend Server** — The bundled Python server starts automatically on port 17593.

> [!NOTE]
> First generation will be slower due to model downloads. Subsequent runs use cached models.

## System Requirements

### Minimum

- **Mac:** Apple Silicon (M1 or later)
- **OS:** macOS 11+
- **RAM:** 8GB
- **Storage:** 5GB free space (for models and data)

### Recommended

- **RAM:** 16GB+
- **Storage:** 10GB+ free space

> [!NOTE]
> Inference runs locally with MLX on the Apple Silicon GPU. Larger models need more RAM.

## Verification

After installation, verify everything works:

1. Launch Voicebox YK
2. Check the server status indicator in the bottom-left corner (should be green)
3. Navigate to **Profiles** and create a test profile
4. Generate a short audio clip to verify the TTS engine works

> [!TIP]
> If you see a green status indicator and can generate audio, you're all set!

## Next Steps

- **[Quick Start Guide](Quick-Start)** — Create your first voice profile and generate speech
