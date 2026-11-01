# Upstream cleanup: research

Date: 2026-11-01. Base commit: `b28dc8c` (v0.7.1).
Upstream: `jamiepine/voicebox`, site `voicebox.sh`. Our repo: `koval-yurko/voicebox`, app "Voicebox YK".

## 1. Current state

- GitHub does **not** show `koval-yurko/voicebox` as a fork (`fork: false`, no parent). No support ticket is needed.
- GitHub repo settings still use upstream text: homepage `voicebox.sh`, description "The open-source AI voice studio. Clone, dictate, create.". Change both in the web UI (Settings → General).
- Wiki is enabled (`has_wiki: true`). The wiki has no pages yet.
- The updater already points to our releases (`tauri.conf.json:64`). The bundle id is ours (`com.koval-yurko.voicebox-yk`).
- `git grep -i 'jamiepine|voicebox\.sh'` outside `docs/` gives 234 hits. 186 of them are in `CHANGELOG.md`.

## 2. Legal limit (read first)

`LICENSE` is MIT, "Copyright (c) 2026 Voicebox Contributors". MIT requires that the copyright and permission notice stay in all copies.

- Keep `LICENSE` text. You can add a second line: `Copyright (c) 2026 Yurii Kovalchuk` for our changes.
- Keep one short credit line in `README.md` ("Based on Voicebox by Jamie Pine, MIT"). This is not strictly required, but it is honest and costs nothing.
- Keep `RESPONSIBLE_USE.md` (voice-cloning ethics, still valid).

## 3. Inventory and decisions

### 3.1 Repo root files

| File | Upstream content | Decision |
|---|---|---|
| `README.md` | Badges (downloads, stars, Trendshift, DeepWiki), voicebox.sh links, download table (Windows/Intel/Linux), "Built with Capy" sponsor, upstream roadmap, `git clone jamiepine/...` | **Rewrite** (see section 5) |
| `CHANGELOG.md` | Upstream history ≤ 0.6.0 with links to upstream PRs | **Keep** old entries (correct attribution). Add a note above 0.6.0: "Entries below are from upstream jamiepine/voicebox". Our entries use our compare links |
| `CONTRIBUTING.md` | Links to `docs/PROJECT_STATUS.md`, `docs/content/...`, `docs/AUTOUPDATER_QUICKSTART.md`, `docs/public` | **Change**: point to wiki pages, or cut to a short "how to build and test" |
| `SECURITY.md` | `security@voicebox.sh` | **Change** to our contact (GitHub private vulnerability report is simplest) |
| `LICENSE` | see section 2 | **Keep**, optional extra copyright line |
| `.github/assets/capy-wordmark-*.svg` | Sponsor logo | **Remove** with the Capy section |
| `.github/assets/*.webp` | Screenshots, icon | **Keep** (or replace with Voicebox YK screenshots later) |
| `.dockerignore:8` | `docs/` | **Remove** the line after `docs/` is gone (harmless if kept) |

### 3.2 App (user-visible)

| Place | Upstream content | Decision |
|---|---|---|
| `app/src/components/ServerTab/AboutPage.tsx:71,96,113,140` | Author link `github.com/jamiepine`, `buymeacoffee.com/jamiepine`, repo link, LICENSE link | **Change** repo + license links to `koval-yurko/voicebox`. **Remove** Buy Me a Coffee. Author: change to "Based on Voicebox by Jamie Pine" credit |
| `app/src/components/ServerTab/GeneralPage.tsx:77,85` | Docs card → `docs.voicebox.sh` | **Change** to `https://github.com/koval-yurko/voicebox/wiki` |
| `app/src/components/ServerTab/GeneralPage.tsx:90-106` + `settings.general.discord.*` in 9 locale files | Upstream Discord | **Remove** card and i18n keys (or change to GitHub Issues) |
| `app/src/components/ServerTab/CloudSection.tsx:141` | `voicebox.sh/account` | Cloud is off by default (`VOICEBOX_CLOUD_ENABLED`). **Keep** for now; removal of the cloud feature is a separate card |

### 3.3 Backend

| Place | Upstream content | Decision |
|---|---|---|
| `backend/config.py:149-159`, `routes/cloud.py:12`, `database/models.py:251`, `tests/test_cloud_gate.py:5` | Cloud defaults `voicebox.sh`, `api.voicebox.sh` | **Keep** (gated off). Same separate card as CloudSection |
| `backend/services/cuda.py:31`, `services/rocm.py:32`, `tests/test_rocm_download.py:142` | GPU backend downloads from upstream releases | We ship Apple Silicon only, so these paths do not run. **Keep** (upstream binaries are valid), or remove CUDA/ROCm in a separate card |
| `backend/backends/base.py:98`, `tests/test_force_cpu_env.py:4` | Comments that cite `docs/content/...` | **Change** to the wiki page name |

### 3.4 Tooling and metadata

| Place | Upstream content | Decision |
|---|---|---|
| `.agents/skills/release-bump/SKILL.md:56-57`, `draft-release-notes/SKILL.md:68` | Upstream compare/PR URLs | **Change** to `koval-yurko/voicebox` |
| `.agents/skills/add-tts-engine/SKILL.md:14,31` | Reads `docs/content/docs/developer/tts-engines.mdx` | **Change**: move that guide to `docs-internal/` (agents need it in the repo, not in the wiki) |
| `tauri/src-tauri/Cargo.toml:4-7` | Old description "Qwen3-TTS…", `authors = ["you"]`, empty `repository` | **Change** description, authors, repository |
| `tauri/src-tauri/Cargo.toml:58` | Comment about `jamiepine/keytap` crate | **Keep** (real dependency name) |
| `scripts/convert-assets.sh:12` | Converts `docs/public` | **Remove** that entry |

## 4. `docs/` → GitHub wiki

### 4.1 What `docs/` is

- A Next.js + Fumadocs site (`app/`, `components/`, `lib/`, `package.json`, `bun.lock`, `next.config.mjs`, `source.config.ts`, `cli.json`) for `docs.voicebox.sh`. It is not in the root bun workspaces, so removal does not affect the build.
- `docs/content/docs/`: 45 MDX pages. 22 are generated API pages (`api-reference/`, from `openapi.json`). 23 are user guides (`overview/`) and 15 are developer pages (`developer/`).
- `docs/plans/*.md` (6 files) and `docs/PROJECT_STATUS.md`: upstream plans and roadmap.
- `docs/public/`: images and logos for the site.

### 4.2 Recommended split

| Content | Target |
|---|---|
| Site code (Next.js, Fumadocs, `openapi.json`, `scripts/`) | **Delete** |
| `api-reference/` (22 pages) | **Delete**. The running server serves Swagger at `http://localhost:17593/docs` (`just docs`). Add one wiki page "API" that says this |
| `overview/` pages that fit a Mac-only fork | **Wiki** (convert) |
| `overview/` pages for Windows, Linux, Docker, CUDA/ROCm, cloud, remote mode | **Drop** or cut to a note |
| `developer/` pages | **Wiki**, except `tts-engines.mdx` → `docs-internal/` (agent skill uses it) |
| `docs/plans/`, `PROJECT_STATUS.md` | Upstream plans. **Drop**, or move the parts you still want to `docs-internal/` |
| `docs/public/images` used by kept pages | Copy into the wiki repo (`images/`) |

### 4.3 MDX → wiki Markdown

The pages use Fumadocs components. GitHub wiki renders plain GitHub Markdown and some HTML. Mapping:

| MDX (count) | Wiki Markdown |
|---|---|
| `<Callout>` (41) | `> [!NOTE]` / `> [!WARNING]` alert. Check that the wiki renders alerts; else a bold-prefixed quote |
| `<Steps>`/`<Step>` (24/89) | Numbered list or `###` headings |
| `<Cards>`/`<Card>` (24/81) | Bullet list of links |
| `<AccordionGroup>`/`<Accordion>` (13/43) | `<details><summary>…</summary>…</details>` |
| `<Tabs>`/`<Tab>` (4/10) | `####` heading for each tab |
| `<Files>`/`<Folder>`/`<File>` (11/44/78) | Tree in a code block |
| `<APIPage>` (22) | Deleted with `api-reference/` |
| Frontmatter `title` | File name → page title; `description` → first line |
| Internal links `/docs/overview/x` | `[text](X)` with the wiki page name |
| `meta.json` order | `_Sidebar.md` |

Wiki facts:
- The wiki is a separate git repo: `git@github.com:koval-yurko/voicebox.wiki.git`. Create the first page in the web UI once, then clone and push.
- Page name = file name, `-` shows as a space (`Quick-Start.md` → "Quick Start"). The namespace is flat: folders are allowed, but links use only the page name, so names must be unique.
- `_Sidebar.md` and `_Footer.md` give navigation.
- Wiki edits are not reviewed by PR. Keep this in mind if others get write access.

Method: a short one-off script (regex for the mapping above) plus a manual pass on each kept page. The manual pass is needed anyway to remove upstream text (Windows, Intel, Linux, Discord, cloud, port 17493 → 17593).

### 4.4 Proposed wiki pages

`Home`, `Installation` (Apple Silicon DMG, unsigned app → `xattr` note), `Quick-Start`, `Voice-Profiles` (creating + cloning + preset voices), `Generating-Speech`, `Stories`, `Captures-and-Dictation`, `Transcription`, `MCP-Server`, `LiveKit-Integration`, `Troubleshooting`, `API`, `Developer-Setup`, `Building`, `Architecture`, `Autoupdater`, `_Sidebar`.

## 5. Proposed README.md outline

1. Title "Voicebox YK", one-line description, release + license badges for `koval-yurko/voicebox`.
2. "Based on [Voicebox](https://github.com/jamiepine/voicebox) by Jamie Pine (MIT)." One line.
3. What is different in this fork: Mac-only local voice (MLX), TTS/STT worker split, streaming STT, LiveKit plugin, runs next to the real Voicebox (own bundle id and port 17593).
4. Download: link to our latest release, Apple Silicon only, note on the unsigned app.
5. Features: short list (no upstream marketing text).
6. API / MCP: 3-4 example lines, link to wiki "API" and "MCP-Server".
7. Development: `git clone git@github.com:koval-yurko/voicebox.git`, `just setup`, `just dev`, link to wiki "Developer-Setup".
8. Docs: link to the wiki.
9. License: MIT, link to `LICENSE`, link to `RESPONSIBLE_USE.md`.

Remove: Trendshift, DeepWiki, stars/downloads badges, demo video, "Built with Capy", Windows/Intel/Linux download rows, upstream roadmap, links to `docs/`.

## 6. Order of work

1. GitHub settings: description + homepage (web UI, manual).
2. Create the wiki first page in the web UI, then push converted pages.
3. One PR in the repo: README rewrite, delete `docs/`, move `tts-engines.mdx` to `docs-internal/`, fix links in `CONTRIBUTING.md`, `SECURITY.md`, `.agents/skills`, backend comments, `convert-assets.sh`, `.dockerignore`, `Cargo.toml`, AboutPage, GeneralPage + i18n, CHANGELOG note.
4. Separate cards (optional): remove cloud feature; remove CUDA/ROCm download code.

## 7. Open decisions for the user

- Keep a credit line to Jamie Pine in README and About page? (Recommended: yes.)
- Discord card: remove, or replace with a GitHub Issues link?
- `docs/plans/` and `PROJECT_STATUS.md`: drop, or keep parts in `docs-internal/`?
- `SECURITY.md` contact: GitHub private vulnerability report, or an email?
