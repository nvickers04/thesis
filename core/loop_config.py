"""JSON parse toggles for Grok output (used by core.json_parse)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field


class JsonParseConfig(BaseModel):
    json_strip_code_fences: bool = True
    json_repair_on_decode_failure: bool = True
    json_close_truncated_braces: bool = True
    json_normalize_smart_quotes: bool = True
    json_strip_trailing_commas: bool = True


# Alias kept so json_parse imports stay unchanged.
LoopConfig = JsonParseConfig


@lru_cache(maxsize=1)
def get_loop_config() -> JsonParseConfig:
    return JsonParseConfig()


def reload_loop_config() -> JsonParseConfig:
    get_loop_config.cache_clear()
    return get_loop_config()


__all__ = ["JsonParseConfig", "LoopConfig", "get_loop_config", "reload_loop_config"]
