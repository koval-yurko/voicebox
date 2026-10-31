# Move work to `main`, drop `-custom` versions, rename to "Voicebox YK"

Date: 2026-10-31. Status: done 2026-11-01 (version 0.7.0). Port is 17593, not 17494: the TTS/STT worker plan (mlx-multi-process.md) uses 17494/17495.

Goal: the repo is no longer a fork that syncs with `jamiepine/voicebox`.
Work continues on `main`. Versions are plain `0.7.0`, `0.7.1`, `0.8.0`.
The app must install and run next to the real Voicebox app without conflicts.

## 1. Git state today

| Item | State |
|---|---|
| `main` | `8af7efe`, the old upstream mirror |
| `custom/main` | `main` + 12 commits; `main` is its ancestor, so a fast-forward works |
| `-custom` tags | none, local or on `origin` (the bump commit `ed14efa` was not tagged) |
| `origin` tags | none |
| local tags | upstream `v0.1.7` … `v0.5.0` (from the `upstream` remote) |
| remotes | `origin` (koval-yurko), `upstream` (jamiepine) |
| `0.6.0-custom.1` string | `.bumpversion.cfg`, 5× `package.json`, `tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, `bun.lock`, `backend/__init__.py` |

So "clean the custom tags" is only a version-string change. No tag rewrite is needed.
History rewrite (drop `ed14efa`) is possible but gives nothing and needs a force push. Recommendation: keep history, add a new bump commit.

## 2. Git steps

1. On `custom/main`: replace `0.6.0-custom.1` with `0.7.0` in all files of the table (see section 4 for the rename commit first).
   `bump2version` cannot parse `0.6.0-custom.1` with the default regex, so do this one bump by hand. Commit `Bump version: 0.6.0-custom.1 → 0.7.0`.
2. Fast-forward main: `git checkout main && git merge --ff-only custom/main && git push origin main`.
3. Tag: `git tag -a v0.7.0 -m "Release v0.7.0" && git push origin v0.7.0`.
   WARNING: do not use `git push --tags`. `release.yml` runs on every `v*` tag push, and the old upstream tags `v0.1.7`…`v0.5.0` would start release jobs.
4. `git remote remove upstream` (removes the 71 upstream remote branches from the list).
   Optional: delete the upstream tags locally. They are valid markers in the shared history, so keeping them is fine.
5. `git remote set-head origin main` (Paseo diff base; it is `custom/main` now).
6. GitHub web UI: set the default branch to `main`. (`gh` is not logged in on this machine.)
7. After the switch: delete `custom/main` (local and `origin`). Keep or delete `backup/*` and `custom/realtime-tts-stream`.
8. Next releases: `uvx bump2version patch` → `0.7.1`, `uvx bump2version minor` → `0.8.0`. The config already commits and tags `v{new_version}`.
   Add `tauri/src-tauri/Cargo.lock` to `.bumpversion.cfg`, or run `cargo check` after a bump, so the lock file does not drift.

## 3. Conflicts with the real Voicebox app

| # | Conflict | Effect if not changed | Severity |
|---|---|---|---|
| 1 | Server port `17493` | On start, `start_server` (`tauri/src-tauri/src/main.rs:285`) finds any `voicebox*` process on `17493` and **reuses it**. The YK app then talks to the real app's backend (wrong models, wrong data) and can kill it on exit. | critical |
| 2 | Bundle id `sh.voicebox.app` | Same app identity: same `~/Library/Application Support/sh.voicebox.app/` data dir, same webview storage, same TCC permissions. The install also replaces `/Applications/Voicebox.app` if `productName` is the same. | critical |
| 3 | Updater endpoint + pubkey are jamiepine's | The YK app offers the real Voicebox release as an "update" and installs it over the YK build. | critical |
| 4 | `productName` `Voicebox` | Same `.app` name, same Dock and menu name. | high |
| 5 | Global dictation chord | Both apps listen to the same default chord, so both record. | medium (settings only, no code) |
| 6 | MCP server name `voicebox` | An MCP client config cannot hold both servers under one name. | medium |
| 7 | Cargo package `voicebox` → main binary name | Process name is the same; on Windows/Linux `VOICEBOX_BUNDLE_ID` is the exe name. | low |
| 8 | Sidecar names `voicebox-server`, `voicebox-mcp` | Only a problem with the same port. After fix 1 they can stay. | low |

Shared and wanted: the Hugging Face cache (`~/.cache/huggingface`). Models are not downloaded again.

## 4. Changes for the rename (one commit)

Names: product `Voicebox YK`, identifier `com.koval-yurko.voicebox-yk` (any reverse-DNS id you own), port `17593`, MCP name `voicebox-yk`.

Required:

- `tauri/src-tauri/tauri.conf.json`: `productName`, `identifier`, `plugins.updater` (see below).
- `tauri/src-tauri/src/main.rs:124` `SERVER_PORT`; `main.rs:979-983` `VOICEBOX_BUNDLE_ID` (new id on macOS; `voicebox-yk.exe` / `voicebox-yk` if the Cargo name changes).
- `tauri/src-tauri/Cargo.toml` `name = "voicebox-yk"` (check `lib.rs` / `[lib]` name and `build.rs` for the crate name).
- Port `17493` → `17593` in code: `app/src/stores/serverStore.ts:31,53`, `app/src/components/ServerTab/GeneralPage.tsx:129`, `app/src/components/ServerSettings/ConnectionForm.tsx:80`, `backend/app.py:176-177`, `backend/mcp_shim/__main__.py:30`, `backend/routes/cloud.py:38`, `backend/tests/test_cors.py`, `.mcp.json`, `package.json` `dev:server`, `justfile`, `integrations/livekit/voicebox_livekit/{stt,tts}.py`, `integrations/livekit/scripts/*`, `scripts/*`. Docs can follow later.
- Updater: either remove `plugins.updater.endpoints` (no auto-update), or point it to `https://github.com/koval-yurko/voicebox/releases/latest/download/latest.json` with your own key (`bun tauri signer generate`, new `pubkey`, secrets `TAURI_SIGNING_PRIVATE_KEY*` in the repo).
- `backend/mcp_server/server.py:28` and `app/src/components/ServerTab/MCPPage.tsx:84,105`: MCP name `voicebox-yk`.

Cosmetic / optional:

- `tauri/src-tauri/Info.plist` usage strings ("Voicebox YK needs microphone access…"). The user sees them in the permission prompt.
- `.github/workflows/release.yml:229` `releaseName`; Apple signing secrets do not exist in this repo, so the macOS job builds unsigned.
- `justfile:321-333`, `CONTRIBUTING.md`: `sh.voicebox.app` paths for CUDA (Windows/Linux only).
- A different icon, so the Dock shows which app is which.
- `AboutPage.tsx` links to jamiepine (credit; keep).

## 5. After the first YK install

1. Grant again: Microphone, Accessibility, Input Monitoring (new bundle id = new TCC entries).
2. Copy data if you want the old profiles and history: quit both apps, then copy `~/Library/Application Support/sh.voicebox.app/` to `~/Library/Application Support/<new id>/`.
3. Change the dictation chord in one of the two apps.
4. Update MCP client configs and the LiveKit agent to port `17593`.
5. Dev servers: still use a temp `--data-dir`, and a port that is not `17493` or `17593`.
