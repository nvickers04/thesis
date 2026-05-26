"""
Pure technical indicator helpers (SMA/MA/EMA) and VIX parsing.

No I/O except optional yfinance VIX fallback — DataProvider orchestrates fetching
candles/quotes and passes close prices into these functions for easy unit testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class VixSnapshot:
    """VIX index level snapshot."""

    symbol: str
    last: Optional[float]
    change_pct: Optional[float]
    source: str


def sma(closes: Sequence[float], period: int) -> Optional[float]:
    """
    Simple moving average of the last ``period`` closes.

    Args:
        closes: Oldest → newest price series.
        period: Lookback length (must be >= 1).

    Returns:
        SMA value or None if insufficient data.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    series = [float(c) for c in closes if c is not None]
    if len(series) < period:
        return None
    window = series[-period:]
    return sum(window) / period


def ma(closes: Sequence[float], period: int) -> Optional[float]:
    """Alias for :func:`sma` (moving average in this project means simple MA)."""
    return sma(closes, period)


def ema(closes: Sequence[float], period: int) -> Optional[float]:
    """
    Exponential moving average over ``closes`` (uses full series seed).

    Returns None if fewer than ``period`` points.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    series = [float(c) for c in closes if c is not None]
    if len(series) < period:
        return None
    k = 2.0 / (period + 1)
    value = sum(series[:period]) / period
    for price in series[period:]:
        value = price * k + value * (1.0 - k)
    return value


def price_vs_sma_pct(last_price: float, sma_value: float) -> Optional[float]:
    """Percent distance of price above/below SMA (positive = above)."""
    if sma_value == 0:
        return None
    return ((last_price - sma_value) / sma_value) * 100.0


def parse_vix_from_quote_dict(raw: dict) -> Optional[VixSnapshot]:
    """Build :class:`VixSnapshot` from a normalized quote dict."""
    if not raw:
        return None
    last = raw.get("last") or raw.get("mid") or raw.get("close")
    if last is None:
        return None
    return VixSnapshot(
        symbol="VIX",
        last=float(last),
        change_pct=float(raw["change_pct"]) if raw.get("change_pct") is not None else None,
        source=str(raw.get("source", "unknown")),
    )


def fetch_vix_yfinance() -> Optional[VixSnapshot]:
    """
    Fetch VIX via yfinance ``^VIX`` (pure side-effecting fallback for DataProvider).

    Kept isolated so tests can mock/monkeypatch this single function.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker("^VIX")
        last = None
        prev = None
        try:
            fi = ticker.fast_info
            last = getattr(fi, "last_price", None) or getattr(fi, "lastPrice", None)
            prev = getattr(fi, "previous_close", None) or getattr(fi, "previousClose", None)
        except Exception:
            pass
        if last is None:
            hist = ticker.history(period="5d")
            if hist is not None and not hist.empty:
                last = float(hist["Close"].iloc[-1])
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
        if last is None:
            return None
        change_pct = None
        if prev and float(prev) > 0:
            change_pct = ((float(last) - float(prev)) / float(prev)) * 100.0
        return VixSnapshot(symbol="VIX", last=float(last), change_pct=change_pct, source="yfinance")
    except Exception:
        return None
