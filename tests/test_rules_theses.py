"""Unit tests for pure rules helpers (no network)."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from theses_rules.rules_based_theses import (
    AutomationBoomLeveragedThesis,
    DefenseGrowthLeveragedThesis,
    LeveragedOvernightDriftThesis,
    LunarOptionsSwingThesis,
    adaptive_spread_width,
    contracts_for_risk_budget,
    hold_signal,
    is_monthly_overlay_window,
    is_quarterly_rebalance_window,
    is_run_time_1555_et,
    option_pnl_pct,
    pick_nearest_delta_call,
    pick_otm_call_by_strike_pct,
    previous_close_above_sma,
    signal_to_execution_decision,
    vix_below_threshold,
)

ET = ZoneInfo("America/New_York")


def _dt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def test_hold_signal_shape():
    sig = hold_signal("test")
    assert sig["action"] == "hold"
    assert sig["quantity"] == 0
    assert sig["option_contract"] is None


def test_pick_nearest_delta_call_band():
    contracts = [
        {"side": "call", "expiration": "20260620", "strike": 100, "dte": 6, "delta": 0.48, "mid": 2.5},
        {"side": "call", "expiration": "20260620", "strike": 105, "dte": 6, "delta": 0.35, "mid": 1.2},
    ]
    pick = pick_nearest_delta_call(
        contracts, min_dte=5, max_dte=7, target_delta=0.50, min_delta=0.45, max_delta=0.55
    )
    assert pick is not None
    assert pick["strike"] == 100


def test_lunar_entry_gate():
    ok, _ = LunarOptionsSwingThesis.lunar_entry_allowed(True)
    assert ok is True
    ok, reason = LunarOptionsSwingThesis.lunar_entry_allowed(False)
    assert ok is False
    assert "new-moon" in reason


def test_overnight_1555_window():
    at_1555 = _dt(2026, 5, 27, 15, 55)
    ok, _ = LeveragedOvernightDriftThesis.entry_allowed(
        when=at_1555, at_1555=is_run_time_1555_et(at_1555), weekday=True
    )
    assert ok is True
    too_early = _dt(2026, 5, 27, 15, 30)
    ok, reason = LeveragedOvernightDriftThesis.entry_allowed(
        when=too_early, at_1555=is_run_time_1555_et(too_early), weekday=True
    )
    assert ok is False
    assert "15:55" in reason


def test_previous_close_above_sma():
    closes = [100.0] * 50 + [105.0, 110.0]
    ok, reason, prior, sma_val = previous_close_above_sma(closes, period=50)
    assert ok is True
    assert prior == 105.0
    assert sma_val is not None and abs(sma_val - 100.1) < 0.01
    assert ">" in reason


def test_vix_threshold():
    ok, _ = vix_below_threshold(22.5, 30.0)
    assert ok is True
    ok, reason = vix_below_threshold(31.0, 30.0)
    assert ok is False
    assert "VIX" in reason


def test_contracts_for_risk_budget():
    qty = contracts_for_risk_budget(100_000, premium_per_share=2.5, risk_pct=2.0, cash=50_000)
    # 2% of 100k = 2000; 250/contract => 8
    assert qty == 8


def test_option_pnl_pct():
    assert option_pnl_pct(2.0, 3.2) == pytest.approx(60.0)


def test_signal_to_execution_decision_take_profit():
    decision = signal_to_execution_decision(
        {
            "action": "buy",
            "symbol": "SPY",
            "option_contract": {"strategy": "long_call", "expiration": "20260620", "right": "C", "strike": 580},
            "quantity": 2,
            "limit_price": 3.5,
            "reason": "test",
            "take_profit_pct": 60.0,
        }
    )
    assert decision["quantity"] == 2
    assert decision["take_profit_pct"] == 60.0


def test_quarterly_rebalance_window():
    jan = _dt(2026, 1, 5, 10, 0)
    ok, reason = is_quarterly_rebalance_window(jan)
    assert ok is True
    assert "Q1" in reason
    feb = _dt(2026, 2, 5, 10, 0)
    ok, _ = is_quarterly_rebalance_window(feb)
    assert ok is False


def test_monthly_overlay_window():
    ok, _ = is_monthly_overlay_window(_dt(2026, 3, 3, 10, 0))
    assert ok is True
    ok, _ = is_monthly_overlay_window(_dt(2026, 3, 15, 10, 0))
    assert ok is False


def test_pick_otm_call_by_strike_pct():
    spot = 100.0
    contracts = [
        {"side": "call", "expiration": "20260718", "strike": 103.5, "dte": 35, "mid": 1.5},
        {"side": "call", "expiration": "20260718", "strike": 110.0, "dte": 35, "mid": 0.8},
    ]
    pick = pick_otm_call_by_strike_pct(contracts, spot, min_dte=30, max_dte=45, min_otm_pct=3, max_otm_pct=5)
    assert pick is not None
    assert pick["strike"] == 103.5


def test_adaptive_spread_width():
    assert adaptive_spread_width(200) == 6.0
    assert adaptive_spread_width(50) == 5.0


def test_contracts_for_risk_3pct():
    qty = contracts_for_risk_budget(100_000, premium_per_share=4.0, risk_pct=3.0, cash=50_000)
    assert qty == 7


def test_defense_automation_risk_pct():
    assert DefenseGrowthLeveragedThesis().risk_per_trade_pct == 3.0
    assert AutomationBoomLeveragedThesis().risk_per_trade_pct == 3.0
