"""
Polygon.io REST client — options chain snapshots with Greeks.

Used by :class:`data.data_provider.DataProvider` for delta-precise option
chain selection. MarketData.app remains the primary source for quotes,
candles, and ATR; Polygon is optional and activated when ``POLYGON_API_KEY``
is set.

API docs: https://polygon.io/docs/options/get_v3_snapshot_options__underlyingasset
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.polygon.io"


def _normalize_api_key(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s or None


class PolygonClient:
    """
    Thin async HTTP client for Polygon options snapshots.

    The thesis trader uses the **options snapshot** endpoint because it returns
    live greeks (delta, gamma, theta, vega) suitable for strike selection.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = _normalize_api_key(api_key or os.environ.get("POLYGON_API_KEY"))
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_options_chain_snapshot(
        self,
        ticker: str,
        *,
        expiration_range_days: int = 45,
        min_delta: float = 0.40,
        max_delta: float = 0.60,
    ) -> Optional[dict[str, Any]]:
        """
        Fetch options chain snapshot and filter by expiration window + delta band.

        Returns a dict compatible with DataProvider chain conversion:
        ``{"contracts": [...], "source": "polygon", "symbol": ticker}``.

        Delta filter uses **absolute delta** so both calls (+) and puts (-) near
        0.40–0.60 moneyness are included.
        """
        if not self.is_configured:
            logger.debug("PolygonClient: POLYGON_API_KEY not configured")
            return None

        sym = ticker.upper().strip()
        today = date.today()
        max_exp = today + timedelta(days=int(expiration_range_days))

        client = await self._get_client()
        url: Optional[str] = f"{API_BASE}/v3/snapshot/options/{sym}"
        params: dict[str, Any] = {"limit": 250, "apiKey": self.api_key}

        contracts: list[dict[str, Any]] = []
        pages = 0

        try:
            while url and pages < 20:
                pages += 1
                if url.startswith(API_BASE):
                    resp = await client.get(url, params=params if pages == 1 else None)
                else:
                    # Pagination next_url already includes apiKey on Polygon
                    resp = await client.get(url)

                if resp.status_code == 401:
                    logger.warning("Polygon API unauthorized — check POLYGON_API_KEY")
                    return None
                if resp.status_code == 403:
                    logger.warning("Polygon API forbidden — plan may not include options snapshots")
                    return None
                if resp.status_code != 200:
                    logger.warning("Polygon snapshot %s HTTP %s: %s", sym, resp.status_code, resp.text[:300])
                    return None

                data = resp.json()
                for item in data.get("results") or []:
                    contract = _parse_snapshot_contract(sym, item, today, max_exp, min_delta, max_delta)
                    if contract is not None:
                        contracts.append(contract)

                url = data.get("next_url")
                params = None  # next_url is self-contained

            if not contracts:
                logger.info("Polygon: no contracts in delta/DTE window for %s", sym)
                return {"contracts": [], "source": "polygon", "symbol": sym}

            logger.info(
                "Polygon chain %s: %d contracts (DTE<=%d, |delta| %.2f-%.2f)",
                sym,
                len(contracts),
                expiration_range_days,
                min_delta,
                max_delta,
            )
            return {"contracts": contracts, "source": "polygon", "symbol": sym}

        except Exception as exc:
            logger.warning("Polygon snapshot failed for %s: %s", sym, exc)
            return None


def _parse_snapshot_contract(
    symbol: str,
    item: dict[str, Any],
    today: date,
    max_exp: date,
    min_delta: float,
    max_delta: float,
) -> Optional[dict[str, Any]]:
    """Map one Polygon snapshot row to our normalized contract dict."""
    details = item.get("details") or {}
    greeks = item.get("greeks") or {}
    day = item.get("day") or {}

    exp_raw = details.get("expiration_date") or details.get("expiration")
    if not exp_raw:
        return None
    try:
        exp_date = datetime.strptime(str(exp_raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    if exp_date < today or exp_date > max_exp:
        return None

    delta = greeks.get("delta")
    if delta is None:
        return None
    abs_delta = abs(float(delta))
    if abs_delta < min_delta or abs_delta > max_delta:
        return None

    ctype = str(details.get("contract_type") or details.get("type") or "").lower()
    side = "call" if ctype in ("call", "c") else "put" if ctype in ("put", "p") else ""

    strike = float(details.get("strike_price") or details.get("strike") or 0)
    bid = (item.get("last_quote") or {}).get("bid")
    ask = (item.get("last_quote") or {}).get("ask")
    if bid is None:
        bid = day.get("bid")
    if ask is None:
        ask = day.get("ask")
    last = day.get("close") or day.get("last")

    bid_f = float(bid) if bid is not None else None
    ask_f = float(ask) if ask is not None else None
    mid = (bid_f + ask_f) / 2 if bid_f is not None and ask_f is not None else None

    dte = (exp_date - today).days
    exp_yyyymmdd = exp_date.strftime("%Y%m%d")

    return {
        "option_symbol": details.get("ticker") or item.get("ticker") or "",
        "underlying": symbol,
        "strike": strike,
        "side": side,
        "expiration": exp_yyyymmdd,
        "dte": dte,
        "bid": bid_f,
        "ask": ask_f,
        "mid": mid,
        "last": float(last) if last is not None else None,
        "volume": int(day.get("volume") or 0),
        "open_interest": int((item.get("open_interest") or {}).get("open_interest") or 0),
        "delta": float(delta),
        "gamma": float(greeks["gamma"]) if greeks.get("gamma") is not None else None,
        "theta": float(greeks["theta"]) if greeks.get("theta") is not None else None,
        "vega": float(greeks["vega"]) if greeks.get("vega") is not None else None,
        "iv": float(item.get("implied_volatility")) if item.get("implied_volatility") is not None else None,
    }


_client: Optional[PolygonClient] = None


def get_polygon_client() -> PolygonClient:
    """Process-wide Polygon client singleton."""
    global _client
    if _client is None:
        _client = PolygonClient()
    return _client


async def get_options_chain_snapshot(
    ticker: str,
    *,
    expiration_range_days: int = 45,
    min_delta: float = 0.40,
    max_delta: float = 0.60,
) -> Optional[dict[str, Any]]:
    """Convenience wrapper used by DataProvider."""
    return await get_polygon_client().get_options_chain_snapshot(
        ticker,
        expiration_range_days=expiration_range_days,
        min_delta=min_delta,
        max_delta=max_delta,
    )
