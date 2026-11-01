#!/usr/bin/env python3
"""One-off: convert docs/content/docs MDX pages into GitHub wiki Markdown.

Usage:
  python3 scripts/mdx-to-wiki.py                 # writes wiki/
  python3 scripts/mdx-to-wiki.py --check-links wiki

WARNING: wiki/ pages were hand-edited after generation; a rerun overwrites them.
shortcut: built for the Fumadocs components used in docs/ at v0.7.1; delete with docs/.
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/content/docs"
OUT = ROOT / "wiki"
REPO = "https://github.com/koval-yurko/voicebox"

# slug -> wiki page name. Order = sidebar order.
PAGES = {
    "index": "Home",
    "overview/introduction": "Introduction",
    "overview/installation": "Installation",
    "overview/quick-start": "Quick-Start",
    "overview/creating-voice-profiles": "Creating-Voice-Profiles",
    "overview/voice-cloning": "Voice-Cloning",
    "overview/preset-voices": "Preset-Voices",
    "overview/voice-personalities": "Voice-Personalities",
    "overview/generating-speech": "Generating-Speech",
    "overview/generation-history": "Generation-History",
    "overview/stories-editor": "Stories-Editor",
    "overview/building-stories": "Building-Stories",
    "overview/recording-transcription": "Recording-and-Transcription",
    "overview/dictation": "Dictation",
    "overview/captures": "Captures",
    "overview/mcp-server": "MCP-Server",
    "overview/troubleshooting": "Troubleshooting",
    "developer/setup": "Developer-Setup",
    "developer/architecture": "Architecture",
    "developer/building": "Building",
    "developer/autoupdater": "Autoupdater",
    "developer/voice-profiles": "Dev-Voice-Profiles",
    "developer/tts-generation": "Dev-TTS-Generation",
    "developer/effects-pipeline": "Dev-Effects-Pipeline",
    "developer/history": "Dev-History",
    "developer/stories": "Dev-Stories",
    "developer/transcription": "Dev-Transcription",
    "developer/audio-channels": "Dev-Audio-Channels",
    "developer/model-management": "Dev-Model-Management",
}
# Pages not migrated: link target outside the wiki, or None = keep the text, drop the link.
EXTERNAL = {
    "developer/contributing": f"{REPO}/blob/main/CONTRIBUTING.md",
    "developer/tts-engines": f"{REPO}/blob/main/docs-internal/tts-engines.mdx",
    "overview/docker": None,
    "overview/gpu-acceleration": None,
    "overview/remote-mode": None,
}

CALLOUT = {"info": "NOTE", "note": "NOTE", "tip": "TIP", "warn": "WARNING",
           "warning": "WARNING", "error": "CAUTION", "danger": "CAUTION", "success": "TIP"}


def resolve(href):
    """Map a docs href to a wiki target. Returns (target or None, keep_link)."""
    if not href.startswith("/"):
        return href
    path, _, anchor = href.partition("#")
    slug = path.strip("/").removeprefix("docs/")
    anchor = f"#{anchor}" if anchor else ""
    if slug in PAGES:
        return PAGES[slug] + anchor
    if slug.startswith("api-reference"):
        return "API"
    if slug.startswith("images/"):
        return slug
    if slug in EXTERNAL:
        return EXTERNAL[slug]
    raise ValueError(f"unknown link {href}")


def fix_links(text):
    def md(m):
        target = resolve(m.group(2))
        return m.group(1) if target is None else f"[{m.group(1)}]({target})"
    return re.sub(r"(?<!!)\[([^\]]+)\]\((/[^)\s]*)\)", md, text)


def convert(src):
    text = src.read_text()
    fm, body = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S).groups()
    desc = re.search(r'^description:\s*"?(.*?)"?\s*$', fm, re.M)
    # Card icons are inline SVG JSX; drop them.
    body = re.sub(r"\s*icon=\{<svg.*?</svg>\}", "", body, flags=re.S)
    body = re.sub(r"!\[([^\]]*)\]\(/images/", r"![\1](images/", body)

    out, stack, step_no, fence, card_buf = [], [], [], None, None
    for raw in body.split("\n"):
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())

        def emit(line):
            # Dedent by the innermost component's content indent; quote inside callouts.
            if stack and stack[-1]["dedent"] is None and line.strip():
                stack[-1]["dedent"] = len(line) - len(line.lstrip())
            cut = stack[-1]["dedent"] if stack and stack[-1]["dedent"] is not None else 0
            lead = len(line) - len(line.lstrip())
            line = line[min(cut, lead):]
            if card_buf is not None:
                card_buf.append(line)
                return
            if any(f["tag"] == "Callout" for f in stack):
                line = "> " + line if line else ">"
            out.append(line)

        if fence is not None:
            emit(raw)
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```"):
            fence = stripped[:3]
            emit(raw)
            continue

        m = re.match(r"<(\w+)([^>]*?)(/?)>(.*)$", stripped)
        close = re.match(r"</(\w+)>$", stripped)
        tag = m.group(1) if m else close.group(1) if close else None
        known = {"Callout", "Cards", "Card", "Steps", "Step", "AccordionGroup",
                 "Accordion", "Tabs", "Tab", "Files", "Folder", "File"}
        if tag not in known:
            emit(raw)
            continue
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2))) if m else {}
        selfclose = bool(m and m.group(3))
        rest = m.group(4) if m else ""

        if close:
            frame = stack.pop()
            if tag == "Steps":
                step_no.pop()
            elif tag == "Accordion":
                out.extend(["", "</details>", ""])
            elif tag == "Card":
                lines = "\n".join(card_buf).strip().split("\n")
                card_buf = None
                # One plain line goes inline; anything longer stays as an indented list body.
                inline = len(lines) == 1 and not lines[0].startswith("```")
                content = lines[0] if inline else ""
                title, href = frame["title"], frame["href"]
                if href is None:
                    item = f"- **{title}**"
                else:
                    target = resolve(href)
                    item = None if target is None else f"- **[{title}]({target})**"
                if item:
                    out.append(item + (f" — {content}" if content else ""))
                    if not inline:
                        out.extend([""] + [f"  {x}" if x else "" for x in lines] + [""])
            elif tag == "Callout":
                out.append("")
            elif tag == "Files":
                out.extend(["```", ""])
            continue

        # Opening tags.
        if tag == "Files":
            out.extend(["", "```"])
            stack.append({"tag": tag, "dedent": None, "depth": 0})
        elif tag in ("Folder", "File"):
            depth = sum(1 for f in stack if f["tag"] == "Folder")
            name = attrs.get("name", "")
            out.append("  " * depth + name + ("/" if tag == "Folder" else ""))
            if tag == "Folder" and not selfclose:
                stack.append({"tag": tag, "dedent": None})
        elif tag == "Callout":
            kind = CALLOUT.get(attrs.get("type", "info"), "NOTE")
            out.extend(["", f"> [!{kind}]"])
            if rest.endswith("</Callout>"):
                out.extend(["> " + rest[: -len("</Callout>")].strip(), ""])
            else:
                stack.append({"tag": tag, "dedent": None})
        elif tag == "Steps":
            step_no.append(0)
            stack.append({"tag": tag, "dedent": None})
        elif tag == "Step":
            step_no[-1] += 1
            out.extend(["", f"**{step_no[-1]}. {attrs.get('title', '')}**", ""])
            stack.append({"tag": tag, "dedent": None})
        elif tag == "Accordion":
            out.extend(["", "<details>", f"<summary>{attrs.get('title', '')}</summary>", ""])
            stack.append({"tag": tag, "dedent": None})
        elif tag == "Tab":
            out.extend(["", f"**{attrs.get('value', '')}**", ""])
            stack.append({"tag": tag, "dedent": None})
        elif tag == "Card":
            if rest.endswith("</Card>"):
                href = attrs.get("href")
                target = resolve(href) if href else None
                text = rest[: -len("</Card>")].strip()
                if not href or target:
                    title = f"[{attrs['title']}]({target})" if href else attrs["title"]
                    out.append(f"- **{title}**" + (f" — {text}" if text else ""))
            else:
                card_buf = []
                stack.append({"tag": tag, "dedent": None, "title": attrs.get("title", ""),
                              "href": attrs.get("href")})
        else:  # Cards, AccordionGroup, Tabs: containers only
            stack.append({"tag": tag, "dedent": None})

    assert not stack, f"{src}: unclosed {[f['tag'] for f in stack]}"
    md = fix_links("\n".join(out))
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    if desc:
        md = f"_{desc.group(1)}_\n\n{md}"
    return md


def sidebar():
    lines = ["**[Home](Home)**", "", "**Guide**", ""]
    for slug, page in PAGES.items():
        if slug == "overview/troubleshooting":
            lines.append(f"- [{page.replace('-', ' ')}]({page})")
            lines += ["", "**Developer**", ""]
        elif slug != "index":
            lines.append(f"- [{page.replace('Dev-', '').replace('-', ' ')}]({page})")
    lines += ["- [API](API)", f"- [Contributing]({REPO}/blob/main/CONTRIBUTING.md)"]
    return "\n".join(lines) + "\n"


API_PAGE = """The backend serves interactive API docs (Swagger UI) while the app runs:

- Swagger UI: http://localhost:17593/docs
- OpenAPI JSON: http://localhost:17593/openapi.json

From a repo checkout, `just docs` opens the Swagger UI in your browser.
"""


def check_links(folder):
    pages = {p.stem for p in Path(folder).glob("*.md")}
    bad = []
    for p in Path(folder).glob("*.md"):
        for target in re.findall(r"(?<!!)\]\(([^)\s#]+)(?:#[^)]*)?\)", p.read_text()):
            if not target.startswith(("http", "mailto:", "images/")) and target not in pages:
                bad.append(f"{p.name}: {target}")
    print("\n".join(bad) or "all links resolve")
    return 1 if bad else 0


def selftest():
    sample = Path("/tmp/_mdx_selftest.mdx")
    sample.write_text('---\ntitle: "T"\ndescription: "D"\n---\n<Steps>\n  <Step title="A">\n    Do [x](/overview/quick-start#y).\n  </Step>\n</Steps>\n\n<Callout type="warn">\n  Careful\n</Callout>\n\n<Cards>\n  <Card title="Gone" href="/overview/docker">\n    hidden\n  </Card>\n  <Card title="Kept" href="/developer/setup">\n    shown\n  </Card>\n</Cards>\n')
    md = convert(sample)
    assert "**1. A**\n\nDo [x](Quick-Start#y)." in md, md
    assert "> [!WARNING]\n> Careful" in md, md
    assert "Gone" not in md and "- **[Kept](Developer-Setup)** — shown" in md, md
    assert md.startswith("_D_"), md


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check-links"]:
        sys.exit(check_links(sys.argv[2]))
    selftest()
    OUT.mkdir(exist_ok=True)
    for slug, page in PAGES.items():
        (OUT / f"{page}.md").write_text(convert(SRC / f"{slug}.mdx"))
    (OUT / "API.md").write_text(API_PAGE)
    (OUT / "_Sidebar.md").write_text(sidebar())
    (OUT / "images").mkdir(exist_ok=True)
    shutil.copy(ROOT / "docs/public/images/app-screenshot-1.webp", OUT / "images")
    print(f"wrote {len(PAGES) + 2} pages to {OUT}")
