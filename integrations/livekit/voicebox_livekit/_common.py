import os


def ensure_ws_url(base_url: str) -> str:
    """``http://host:port`` → ``ws://host:port`` (no trailing slash)."""
    url = base_url.rstrip("/")
    if url.startswith(("http://", "https://")):
        url = url.replace("http", "ws", 1)
    return url


def auth_headers(api_key: str | None) -> dict[str, str] | None:
    """Bearer header for a Voicebox reached through its API key gate (e.g. a Cloudflare Tunnel)."""
    key = api_key or os.environ.get("VOICEBOX_API_KEY")
    return {"Authorization": f"Bearer {key}"} if key else None
