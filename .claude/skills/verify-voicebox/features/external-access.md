# External access

Clients outside this Mac (LiveKit agents through the Cloudflare Tunnel `voice/tts/stt.kovalchuk.win`) must send an API key. The health check and the API docs stay open. Local clients (the desktop app, local scripts) need no key.

## Sub-features

- `ext-refuse` an external request without a key gets HTTP 401.
- `ext-public` an external request to `/health` works without a key.
- `ext-key` an external request with a valid key works (`Authorization: Bearer <key>` or `X-API-Key`).
- `ext-local` a loopback request without Cloudflare headers works without a key.

## How to get to it (user POV)

- Make a key: `python3 scripts/voicebox_api_key.py --data-dir <data dir> add <name>`. It prints the key once.
- Call any route through the tunnel with the key.
- The server marks a request external when it has a `Cf-Connecting-Ip` or `Cf-Ray` header, or comes from a non-loopback address.

## Driving it with vb-verify

Preconditions:

- `$V doctor` is all `ok`.
- `D="$(cat .verify/current)/data"` is the disposable data dir.

- **Refuse.** Act as a tunnel client without a key. Run `$V curl ext-nokey /profiles -H 'Cf-Ray: verify'`. HTTP 401 and `{"detail":"Invalid or missing API key"}`.
- **Public path.** Ask for health as a tunnel client. Run `$V curl ext-health /health -H 'Cf-Ray: verify'`. HTTP 200.
- **Make a key.** Add a key to the disposable data dir. Run `K=$(python3 scripts/voicebox_api_key.py --data-dir "$D" add verify | tail -1)`. `$K` is a long random string.
- **Use the key.** Repeat the refused call with the key. Run `$V curl ext-key /profiles -H 'Cf-Ray: verify' -H "Authorization: Bearer $K"`. HTTP 200 and a JSON list.
- **Local.** Call without headers. Run `$V curl local /profiles`. HTTP 200.

## Gotchas

- Warning: without `--data-dir`, `scripts/voicebox_api_key.py` writes to the installed app (`~/Library/Application Support/com.koval-yurko.voicebox-yk`). Always pass the disposable data dir.
- `ext-key.cmd` contains the key. The key is valid only for the disposable data dir, which `down` deletes.
- Only the exact paths `/health`, `/docs`, `/redoc` and `/openapi.json` are public. `/health/filesystem` needs a key.
- The server re-reads `api_keys.json` when the file changes. No restart is needed.
- Proven end to end on 2026-11-14 (run `proof-20261114`).
- This recipe does not go through the real tunnel. The real tunnel reaches only the installed app on 17593, so do not use it for verification.
