"""Grok LLM client via the official xAI SDK."""

from __future__ import annotations

import logging
import os
from typing import Optional

from xai_sdk import AsyncClient

from core.prompt_config import get_prompt_config

logger = logging.getLogger(__name__)


class GrokLLM:
    """Single-turn Grok client for thesis decisions."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        pc = get_prompt_config()
        self.model = model or pc.reasoning_model
        self.temperature = pc.llm_temperature if temperature is None else temperature
        self.max_tokens = pc.llm_max_tokens if max_tokens is None else max_tokens

        api_key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
        if not api_key:
            logger.warning("No XAI_API_KEY or GROK_API_KEY found in environment")

        self.client = AsyncClient(api_key=api_key)
        logger.info("GrokLLM ready (model=%s, temp=%s)", self.model, self.temperature)


_grok_llm: Optional[GrokLLM] = None


def get_grok_llm() -> GrokLLM:
    global _grok_llm
    if _grok_llm is None:
        _grok_llm = GrokLLM()
    return _grok_llm


__all__ = ["GrokLLM", "get_grok_llm"]
