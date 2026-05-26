"""
Lunar phase re-exports — backward-compatible facade over :mod:`data.MoonCalculator`.

Prefer ``from data.MoonCalculator import ...`` for new code.
"""

from __future__ import annotations

from data.MoonCalculator import (
    LunarPhaseSnapshot,
    MoonCalculator,
    get_current_lunar_phase,
    is_full_moon_window,
    is_new_moon_window,
)

# Legacy alias used by DataProvider / fetch_data
LunarPhase = LunarPhaseSnapshot


def get_lunar_phase(when=None) -> LunarPhaseSnapshot:
    """Backward-compatible wrapper around :func:`get_current_lunar_phase`."""
    return get_current_lunar_phase(when)


__all__ = [
    "LunarPhase",
    "LunarPhaseSnapshot",
    "MoonCalculator",
    "get_current_lunar_phase",
    "get_lunar_phase",
    "is_full_moon_window",
    "is_new_moon_window",
]
