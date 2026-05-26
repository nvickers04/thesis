"""Grok model and sampling settings for the thesis trader."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field


class PromptConfig(BaseModel):
    """Single-turn Grok settings (no agent / ReAct template)."""

    reasoning_model: str = Field(default="grok-4.3")
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_seed: int = Field(default=42)
    llm_max_tokens: int = Field(default=4096, ge=256, le=8192)


_prompt_override: PromptConfig | None = None


@lru_cache(maxsize=1)
def get_prompt_config() -> PromptConfig:
    return _prompt_override or PromptConfig()


def reload_prompt_config() -> PromptConfig:
    global _prompt_override
    _prompt_override = None
    get_prompt_config.cache_clear()
    return get_prompt_config()


__all__ = ["PromptConfig", "get_prompt_config", "reload_prompt_config"]
