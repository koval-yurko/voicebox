def ensure_ws_url(base_url: str) -> str:
    """``http://host:port`` → ``ws://host:port`` (no trailing slash)."""
    url = base_url.rstrip("/")
    if url.startswith(("http://", "https://")):
        url = url.replace("http", "ws", 1)
    return url
