"""HTTP client from main to the STT worker (``--role main --stt-url``)."""

from pathlib import Path

import httpx

from ..backends import ModelConfig

# No read timeout: a long recording can take minutes. Connect fails fast when the worker is down.
_TIMEOUT = httpx.Timeout(None, connect=5.0)


class WorkerError(RuntimeError):
    pass


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail or response.text or response.reason_phrase)


async def _post(url: str, path: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url + path, **kwargs)
    except httpx.HTTPError as e:
        raise WorkerError(f"STT worker at {url} is not reachable: {e}") from e
    # 202 means the worker started a model download, so there is no transcript yet.
    if response.status_code != 200:
        raise WorkerError(f"STT worker {path} failed ({response.status_code}): {_detail(response)}")
    return response


async def transcribe(url: str, config: ModelConfig, audio_path: str, language: str | None) -> str:
    data = {"model": config.model_name}
    if language:
        data["language"] = language
    with open(audio_path, "rb") as f:
        response = await _post(url, "/transcribe", data=data, files={"file": (Path(audio_path).name, f)})
    return response.json()["text"]


async def load_model(url: str, model_name: str) -> None:
    await _post(url, "/models/load", json={"model_name": model_name})


async def unload_model(url: str, model_name: str) -> bool:
    response = await _post(url, f"/models/{model_name}/unload")
    return "unloaded successfully" in response.json().get("message", "")


async def loaded_models(url: str) -> set[str] | None:
    """Names the worker has loaded, or None when it does not answer."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            response = await client.get(url + "/models/status")
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return {m["model_name"] for m in response.json()["models"] if m.get("loaded")}
