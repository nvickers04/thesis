"""
Historical DataProvider wrapper for backtests.

Pins all market reads to an ``as_of`` UTC datetime using MarketData.app historical
endpoints (``to_date`` / ``date``). Polygon live snapshots are skipped in backtest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from data.data_provider import DataProvider, OptionChain, Quote


class HistoricalDataProvider:
    """Delegate to inner provider with historical date context."""

    def __init__(self, inner: DataProvider, as_of: datetime) -> None:
        self._inner = inner
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        self.as_of = as_of.astimezone(timezone.utc)

    @property
    def as_of_date(self) -> str:
        return self.as_of.strftime("%Y-%m-%d")

    def _history_start(self, days: int) -> str:
        start = self.as_of - timedelta(days=days)
        return start.strftime("%Y-%m-%d")

    def get_candles(
        self,
        symbol: str,
        resolution: str = "D",
        days_back: int = 30,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        fd = from_date or self._history_start(max(days_back + 10, 60))
        td = to_date or self.as_of_date
        return self._inner.get_candles(
            symbol,
            resolution=resolution,
            days_back=days_back,
            from_date=fd,
            to_date=td,
        )

    def get_quote(self, symbol: str) -> Optional[Quote]:
        candles = self.get_candles(symbol, days_back=5)
        if not candles or not candles.close:
            return None
        last = float(candles.close[-1])
        return Quote(
            symbol=symbol.upper(),
            last=last,
            bid=last,
            ask=last,
            volume=int(candles.volume[-1]) if candles.volume else 0,
            change_pct=None,
            source=f"historical:{self.as_of_date}",
            timestamp=self.as_of,
        )

    def get_options_chain(
        self,
        ticker: str,
        expiration_range_days: int = 45,
        min_delta: float = 0.40,
        max_delta: float = 0.60,
        data_source: str = "auto",
    ) -> Optional[OptionChain]:
        sym = ticker.upper()
        chain = self._inner.get_option_chain(
            sym,
            date=self.as_of_date,
            dte_range=(1, expiration_range_days),
        )
        if chain is None:
            return None
        filtered = chain.filter_by_delta(min_delta, max_delta)
        return OptionChain(
            symbol=sym,
            contracts=filtered,
            source=f"historical:{chain.source or 'marketdata'}",
            is_historical=True,
            as_of_date=self.as_of_date,
        )

    def get_option_chain(self, symbol: str, **kwargs: Any) -> Optional[OptionChain]:
        kwargs.setdefault("date", self.as_of_date)
        return self._inner.get_option_chain(symbol, **kwargs)

    def get_sma(self, symbol: str, period: int = 20) -> Optional[float]:
        from data.technicals import sma

        candles = self.get_candles(symbol, days_back=max(period + 10, 30))
        if not candles or not candles.close:
            return None
        return sma(list(candles.close), period)

    def get_ma(self, symbol: str, period: int = 20) -> Optional[float]:
        return self.get_sma(symbol, period)

    def get_vix(self):
        for sym in ("VIX", "^VIX"):
            q = self.get_quote(sym)
            if q and q.last is not None:
                from data.technicals import VixSnapshot

                return VixSnapshot(symbol=sym, last=q.last, change_pct=None, source=q.source)
        return self._inner.get_vix()

    def get_lunar_phase(self, when: Optional[datetime] = None):
        from data.MoonCalculator import get_current_lunar_phase

        return get_current_lunar_phase(when or self.as_of)

    def is_new_moon_window(self, when: Optional[datetime] = None) -> bool:
        from data.MoonCalculator import is_new_moon_window

        return is_new_moon_window(when or self.as_of)

    def is_full_moon_window(self, when: Optional[datetime] = None) -> bool:
        from data.MoonCalculator import is_full_moon_window

        return is_full_moon_window(when or self.as_of)

    def get_atr(self, symbol: str, period: int = 14):
        return self._inner.get_atr(symbol, period)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
