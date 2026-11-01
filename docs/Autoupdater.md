_How Voicebox YK automatic updates work_

## Overview

Voicebox YK uses the Tauri updater to deliver signed updates. The app checks the signature of each update before it installs it.

## How It Works

When Voicebox YK launches (in production Tauri builds only), it checks GitHub Releases for a `latest.json` manifest. If a newer version is available:

1. **Notification** - An update banner appears at the top of the app
2. **Download** - User clicks "Install Now" to download the update package
3. **Verification** - The downloaded package is cryptographically verified using the public key embedded in `tauri.conf.json`
4. **Installation** - After verification, the update is installed
5. **Restart** - The app restarts automatically with the new version

Users can also check for updates manually via **Settings → Check for Updates**.

## Configuration

The updater is configured in `tauri/src-tauri/tauri.conf.json`:

```json
{
  "bundle": {
    "createUpdaterArtifacts": "v1Compatible"
  },
  "plugins": {
    "updater": {
      "pubkey": "<base64 public key>",
      "endpoints": [
        "https://github.com/koval-yurko/voicebox/releases/latest/download/latest.json"
      ]
    }
  }
}
```

**Key settings:**
- `endpoints` - URL of the `latest.json` manifest. It always points to the latest published release.
- `pubkey` - Public key that checks update signatures.
- `createUpdaterArtifacts` - Tells `tauri build` to make the signed `.app.tar.gz` update package.

## Release Manifest

`latest.json` lists the update for each platform. Voicebox YK only ships `darwin-aarch64`:

```json
{
  "version": "0.7.1",
  "notes": "Release notes from CHANGELOG.md",
  "pub_date": "2026-11-01T12:00:00Z",
  "platforms": {
    "darwin-aarch64": {
      "signature": "base64_encoded_signature",
      "url": "https://github.com/koval-yurko/voicebox/releases/download/v0.7.1/<app>_aarch64.app.tar.gz"
    }
  }
}
```

## Signing

Updates must be signed with the Tauri signing key. Without a valid signature the app refuses the update.

1. **Generate keys** (one-time setup):
   ```bash
   bun tauri signer generate -w ~/.tauri/voicebox-yk.key
   ```
   This creates:
   - Private key: `~/.tauri/voicebox-yk.key` (never committed)
   - Public key: `~/.tauri/voicebox-yk.key.pub` (pasted into `pubkey` in `tauri.conf.json`)

2. **Build with signing**:
   - CI sets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` from GitHub Secrets.
   - Local `just build-tauri` uses `~/.tauri/voicebox-yk.key` if the variable is not set. It reads the password from the macOS keychain item `voicebox-yk-tauri-signing`.
   - Tauri signs the update package and writes a `.sig` file next to it.

3. **Verification** - The updater checks the signature against the public key before it installs.

> [!NOTE]
> This is only the Tauri updater signature. The app is not signed with an Apple Developer certificate. macOS Gatekeeper may block the first launch of a downloaded build.

## GitHub Actions Workflow

The release workflow (`.github/workflows/release.yml`) runs on `macos-latest` and:

- Builds the Python server with `scripts/build-server.sh` (MLX backend)
- Builds the Tauri app for `aarch64-apple-darwin` only
- Signs the update package with the Tauri signing key
- Takes the release notes from the matching section of `CHANGELOG.md`
- Publishes the GitHub Release "Voicebox YK v<version>" with the build and `latest.json`

The release is published at once, not as a draft. The updater reads `releases/latest`, which skips drafts.

It runs when you push a `v*` tag, or by hand with `workflow_dispatch`:

```bash
git tag v0.7.1 && git push --tags
```

## Environment Variables

GitHub Actions needs these secrets:

- `TAURI_SIGNING_PRIVATE_KEY` - Content of `~/.tauri/voicebox-yk.key`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` - Password for the key (if set)

## Security

> [!WARNING]
> **Critical:** Never commit the private key. Store it only in GitHub Secrets. The public key in `tauri.conf.json` is safe to commit and distribute.

- Updates are cryptographically signed using Ed25519
- HTTP endpoints are blocked (HTTPS only)
- Signature verification happens before installation
- Failed verification aborts the update

## Troubleshooting

### "Invalid signature" error
- Public key in `tauri.conf.json` doesn't match the private key used to sign
- Signature file wasn't uploaded to the release

### "No update available" when one exists
- `latest.json` version isn't higher than current version
- Wrong endpoint URL in configuration
- The release is still a draft, or GitHub's CDN has not updated yet

### Update check fails in dev mode
The updater only works in production Tauri builds. It doesn't run during `just dev` or web mode.

### Build fails with signing error
- GitHub Secrets aren't set correctly
- Private key file is missing or corrupted
- Key format is wrong (should start with `dW50cnVzdGVkIGNvbW1lbnQ6`)
