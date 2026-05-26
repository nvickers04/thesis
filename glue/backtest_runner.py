"""
Backtest runner — walk historical dates and simulate rules portfolio cycles.

Uses :class:`glue.historical_provider.HistoricalDataProvider` (MarketData.app
historical candles + option chains). Logs to the same JSONL audit file.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from glue.historical_provider import HistoricalDataProvider
from glue.portfolio_manager import PortfolioManager
from glue.rules_runner import build_rules_context, execute_portfolio_plan
from glue.trade_logger import TradeLogger
from theses_rules.rules_based_theses import RuleThesis

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

BACKTEST_START = date(2023, 1, 1)


def iter_business_days(
    start: date,
    end: date,
    *,
    daily: bool = False,
) -> Iterator[date]:
    """Yield weekdays from ``start`` through ``end`` (weekly if not daily)."""
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            if daily:
                yield cur
            elif cur.weekday() == 0:
                yield cur
        cur += timedelta(days=1)


def _asof_dt(d: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


async def run_backtest(
    theses: list[RuleThesis],
    *,
    data_provider: Any,
    cost_tracker: Any,
    trade_log: TradeLogger,
    start: date | None = None,
    end: date | None = None,
    daily: bool = False,
    starting_cash: float = 100_000.0,
) -> dict[str, Any]:
    """
    Simulate rules portfolio from ``start`` (default 2023-01-01) through ``end`` (today).

    Returns summary dict with final NLV, peak, trade count.
    """
    from glue.bootstrap import is_local_paper_sim
    from glue.paper_broker import PaperBroker

    start_d = start or BACKTEST_START
    end_d = end or datetime.now(ET).date()
    trade_log.log_event(
        "backtest_start",
        {
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "daily": daily,
            "theses": [t.id for t in theses],
        },
    )

    broker = PaperBroker(initial_cash=starting_cash, data_provider=data_provider)
    await broker.connect()
    manager = PortfolioManager(theses)
    manager.global_risk.reset_peak(starting_cash)

    steps = 0
    trades = 0
    for d in iter_business_days(start_d, end_d, daily=daily):
        # Morning pass (overnight exits ~9:35 ET)
        for as_of in (_asof_dt(d, 9, 35), _asof_dt(d, 15, 55)):
            hist = HistoricalDataProvider(data_provider, as_of)
            broker.data_provider = hist
            ctx = await build_rules_context(broker, hist, as_of=as_of)
            plan = manager.build_plan(ctx, broker, data_provider=hist)
            trade_log.log_event(
                "backtest_step",
                {
                    "date": d.isoformat(),
                    "as_of": as_of.isoformat(),
                    "global_risk": _risk_dict(plan.global_risk),
                    "raw_count": len(plan.raw_signals),
                    "approved_count": len(plan.actionable),
                },
            )
            executed = await execute_portfolio_plan(
                plan,
                manager=manager,
                gateway=broker,
                data_provider=hist,
                cost_tracker=cost_tracker,
                trade_log=trade_log,
                execute=True,
            )
            trades += len(executed)
            steps += 1

    await broker.refresh_positions()
    summary = {
        "steps": steps,
        "trades": trades,
        "final_nlv": float(broker.net_liquidation),
        "final_cash": float(broker.cash_value),
        "peak_nlv": manager.global_risk.peak_nlv,
    }
    trade_log.log_event("backtest_complete", summary)
    await broker.disconnect()
    logger.info("Backtest complete: %s", summary)
    return summary


def _risk_dict(status: Any | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "drawdown_pct": status.drawdown_pct,
        "cash_pct": status.cash_pct,
        "premium_exposure_pct": status.premium_exposure_pct,
        "block_new_premium": status.block_new_premium,
        "reason": status.reason,
    }
