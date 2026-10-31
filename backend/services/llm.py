"""
LLM inference module - delegates to backend abstraction layer.
"""

from ..backends import LLMBackend, get_llm_backend


def get_llm_model() -> LLMBackend:
    """Get the custom endpoint backend. Raises LLMNotConfiguredError when none is set."""
    return get_llm_backend()
