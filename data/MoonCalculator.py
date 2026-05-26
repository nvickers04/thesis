"""
Astropy-based lunar phase calculator for the thesis trader.

All core logic is implemented as **pure functions** (date in → result out) so unit
tests can pin known ephemeris dates without mocking network or DataProvider.

New-moon bullish window: **14 calendar days** after each new moon (moon age 0–14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Union

import numpy as np
from astropy import units as u
from astropy.coordinates import GeocentricMeanEcliptic, get_body, get_sun
from astropy.time import Time

# Mean synodic month (days)
SYNODIC_MONTH_DAYS = 29.530588853

# Thesis default: trade the waxing half-cycle after new moon
NEW_MOON_BULLISH_WINDOW_DAYS = 14

# Full moon proximity (± days around syzygy at synodic month / 2)
FULL_MOON_HALF_WINDOW_DAYS = 3.0

DateLike = Union[date, datetime, None]


@dataclass(frozen=True)
class LunarPhaseSnapshot:
    """Immutable lunar state at an instant."""

    phase_name: str
    illumination_pct: float  # 0 = new, 100 = full
    moon_age_days: float  # days since last new moon [0, synodic month)
    elongation_deg: float  # Sun–Moon separation (0=new, 180=full)
    phase_angle_deg: float  # Ecliptic phase angle (0=new, 180=full)
    is_new_moon_window: bool
    is_full_moon_window: bool
    source: str = "astropy"
    as_of_utc: Optional[str] = None


def _coerce_datetime(value: DateLike) -> datetime:
    """Normalize date/datetime/None to timezone-aware UTC datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise TypeError(f"Expected date/datetime, got {type(value)!r}")


def _astropy_time(value: DateLike) -> Time:
    """Build Astropy Time in UTC scale."""
    dt = _coerce_datetime(value)
    return Time(dt)


def moon_elongation_deg(when: DateLike) -> float:
    """
    Geocentric Sun–Moon elongation in degrees (0 = new, 180 = full).

    Pure function — uses Astropy ephemerides.
    """
    t = _astropy_time(when)
    sun = get_sun(t)
    moon = get_body("moon", t)
    return float(sun.separation(moon).to("deg").value)


def moon_phase_angle_deg(when: DateLike) -> float:
    """
    Moon phase angle in degrees [0, 360) from ecliptic longitudes.

    0° = new moon, 180° = full moon, 360° wraps to next new moon.
    """
    t = _astropy_time(when)
    frame = GeocentricMeanEcliptic(equinox=t)
    sun_lon = get_sun(t).transform_to(frame).lon.to(u.deg).value
    moon_lon = get_body("moon", t).transform_to(frame).lon.to(u.deg).value
    return float((moon_lon - sun_lon) % 360.0)


def moon_age_days(when: DateLike) -> float:
    """
    Days since last new moon, in [0, SYNODIC_MONTH_DAYS).

    Derived from ecliptic phase angle (Astropy ephemeris).
    """
    angle = moon_phase_angle_deg(when)
    return (angle / 360.0) * SYNODIC_MONTH_DAYS


def illumination_pct(when: DateLike) -> float:
    """Illuminated fraction of the lunar disc as a percentage."""
    angle = moon_phase_angle_deg(when)
    frac = (1.0 - np.cos(np.radians(angle))) / 2.0
    return round(float(frac * 100.0), 2)


def phase_name_from_age(age_days: float) -> str:
    """Human-readable phase label from moon age."""
    a = age_days % SYNODIC_MONTH_DAYS
    if a < 1.85:
        return "New Moon"
    if a < 7.38:
        return "Waxing Crescent"
    if a < 11.07:
        return "First Quarter"
    if a < 14.77:
        return "Waxing Gibbous"
    if a < 16.61:
        return "Full Moon"
    if a < 22.15:
        return "Waning Gibbous"
    if a < 25.84:
        return "Last Quarter"
    if a < SYNODIC_MONTH_DAYS:
        return "Waning Crescent"
    return "New Moon"


def is_new_moon_window(
    when: DateLike,
    window_days: int = NEW_MOON_BULLISH_WINDOW_DAYS,
) -> bool:
    """
    True during the **new-moon bullish window** (default: first 14 days after new moon).

    This is the thesis default for cyclical bullish bias research — waxing phase
    from new moon through two weeks forward.
    """
    if window_days <= 0:
        return False
    return moon_age_days(when) <= float(window_days)


def is_full_moon_window(
    when: DateLike,
    half_window_days: float = FULL_MOON_HALF_WINDOW_DAYS,
) -> bool:
    """
    True when moon age is within ``half_window_days`` of full moon (~day 14.77).
    """
    age = moon_age_days(when)
    full_moon_age = SYNODIC_MONTH_DAYS / 2.0
    return abs(age - full_moon_age) <= float(half_window_days)


def get_current_lunar_phase(when: DateLike = None) -> LunarPhaseSnapshot:
    """
    Full lunar snapshot at ``when`` (default: now UTC).

    Combines elongation, illumination, age, and window flags.
    """
    dt = _coerce_datetime(when)
    age = moon_age_days(dt)
    elong = moon_elongation_deg(dt)
    angle = moon_phase_angle_deg(dt)
    illum = illumination_pct(dt)
    return LunarPhaseSnapshot(
        phase_name=phase_name_from_age(age),
        illumination_pct=illum,
        moon_age_days=round(age, 3),
        elongation_deg=round(elong, 3),
        phase_angle_deg=round(angle, 3),
        is_new_moon_window=is_new_moon_window(dt),
        is_full_moon_window=is_full_moon_window(dt),
        source="astropy",
        as_of_utc=dt.isoformat(),
    )


class MoonCalculator:
    """
    Namespace class for lunar helpers (delegates to module pure functions).

    Prefer importing functions directly in tests; use this class when you want
    a single discoverable API surface.
    """

    SYNODIC_MONTH_DAYS = SYNODIC_MONTH_DAYS
    NEW_MOON_BULLISH_WINDOW_DAYS = NEW_MOON_BULLISH_WINDOW_DAYS
    FULL_MOON_HALF_WINDOW_DAYS = FULL_MOON_HALF_WINDOW_DAYS

    is_new_moon_window = staticmethod(is_new_moon_window)
    is_full_moon_window = staticmethod(is_full_moon_window)
    get_current_lunar_phase = staticmethod(get_current_lunar_phase)
    moon_age_days = staticmethod(moon_age_days)
    moon_elongation_deg = staticmethod(moon_elongation_deg)
    moon_phase_angle_deg = staticmethod(moon_phase_angle_deg)
    illumination_pct = staticmethod(illumination_pct)
