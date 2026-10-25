#!/usr/bin/env python3
"""Manage API keys for external access to the real-time TTS routes.

Keys are stored in <data dir>/api_keys.json (mode 0600). The server re-reads
the file when it changes, so no restart is needed.

Usage:
  python3 scripts/voicebox_api_key.py add <name>      # prints the new key once
  python3 scripts/voicebox_api_key.py list
  python3 scripts/voicebox_api_key.py revoke <name>
Options:
  --data-dir PATH   default: ~/Library/Application Support/sh.voicebox.app
"""

import argparse
import json
import os
import secrets
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "sh.voicebox.app"


def load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"keys": []}


def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add").add_argument("name")
    sub.add_parser("list")
    sub.add_parser("revoke").add_argument("name")
    args = p.parse_args()

    path = args.data_dir / "api_keys.json"
    data = load(path)
    keys = data.setdefault("keys", [])

    if args.cmd == "add":
        if any(k.get("name") == args.name for k in keys):
            raise SystemExit(f"key '{args.name}' already exists; revoke it first")
        key = "vbx_" + secrets.token_urlsafe(32)
        keys.append({"name": args.name, "key": key})
        save(path, data)
        print(f"Added key '{args.name}' to {path}")
        print(key)
    elif args.cmd == "list":
        for k in keys:
            print(f"{k.get('name')}\t{k.get('key', '')[:8]}…")
        if not keys:
            print(f"no keys in {path} (all external requests are refused)")
    elif args.cmd == "revoke":
        left = [k for k in keys if k.get("name") != args.name]
        if len(left) == len(keys):
            raise SystemExit(f"no key named '{args.name}'")
        data["keys"] = left
        save(path, data)
        print(f"Revoked '{args.name}'")


if __name__ == "__main__":
    main()
