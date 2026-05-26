"""
Minimal memory stubs for the thesis trader.

The copied ABC execution layer expects a ``memory`` package for optional
persistence (Postgres in the full ABC stack). This project does not use a
database — every function here is a safe no-op or in-memory shim so imports
succeed without Postgres.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_pending_order_context: dict[str, dict] = {}
_pending_graduated_params: dict[str, int] = {}


class _StubDB:
    """Tiny stand-in when tools ask for ``get_db()``."""

    def execute(self, *args: Any, **kwargs: Any) -> "_StubDB":
        return self

    def commit(self) -> None:
        return None

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


def get_db() -> _StubDB:
    return _StubDB()


def get_research_config(key: str, default: float) -> float:
    return default


def set_research_config(key: str, value: float, reason: str = "", *, log: bool = True) -> None:
    logger.debug("set_research_config(%s=%s) ignored (no DB)", key, value)


def write_latest_quote(quote: Any, source: str = "ibkr") -> None:
    return None


def read_latest_quote(symbol: str) -> dict | None:
    return None


def get_execution_cost(symbol: str | None = None) -> dict:
    return {"slippage_bps": 0.0, "source": "stub"}


def record_trade(*args: Any, **kwargs: Any) -> None:
    return None


def record_iv_snapshot(*args: Any, **kwargs: Any) -> None:
    return None


def compute_iv_rank_percentile(*args: Any, **kwargs: Any) -> dict:
    return {}


def get_calibrated_slippage(symbol: str) -> float:
    return 0.0


def get_graduated_params(symbol: str) -> dict:
    return {}


def _time_bucket(ts_iso: str | None) -> str:
    return "unknown"


def _atr_bucket(atr_pct: float | None) -> str:
    return "unknown"


def set_pending_graduated_param(symbol: str, param_id: int) -> None:
    _pending_graduated_params[symbol.upper()] = param_id


def get_pending_graduated_param(symbol: str) -> int | None:
    return _pending_graduated_params.get(symbol.upper())


def set_pending_order_context(symbol: str, context: dict) -> None:
    _pending_order_context[symbol.upper()] = dict(context)


def get_pending_order_context(symbol: str) -> dict:
    return dict(_pending_order_context.get(symbol.upper(), {}))


def insert_execution_snapshot(*args: Any, **kwargs: Any) -> None:
    return None


def update_execution_snapshot_fill(*args: Any, **kwargs: Any) -> None:
    return None
