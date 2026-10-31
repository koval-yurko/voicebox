"""LLM inference endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from .. import models
from ..backends import LLMNotConfiguredError
from ..services import llm

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/llm/generate", response_model=models.LLMGenerateResponse)
async def llm_generate(request: models.LLMGenerateRequest):
    """Run a single-turn chat completion on the custom OpenAI-compatible endpoint.

    There is no built-in LLM: without an endpoint in capture settings this
    returns 400.
    """
    try:
        backend = llm.get_llm_model()
    except LLMNotConfiguredError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    examples: list[tuple[str, str]] | None = None
    if request.examples:
        for pair in request.examples:
            if len(pair) != 2:
                raise HTTPException(
                    status_code=400,
                    detail="Each example must be a [user, assistant] pair",
                )
        examples = [(pair[0], pair[1]) for pair in request.examples]

    try:
        text = await backend.generate(
            prompt=request.prompt,
            system=request.system,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            examples=examples,
        )
        return models.LLMGenerateResponse(text=text, model_size=backend.model_size)
    except RuntimeError as e:
        # The remote backend raises a deliberately short message naming
        # the endpoint and status (no local paths), so the UI can show
        # the user what to fix.
        logger.error("Custom LLM endpoint failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        # The backend exception text can include stack frames — log it
        # server-side and hand the client a generic message.
        logger.exception("LLM generate failed")
        raise HTTPException(status_code=500, detail="LLM generation failed") from e
