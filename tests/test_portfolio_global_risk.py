"""Tests for PortfolioManager and global risk gates."""

from __future__ import annotations

from glue.global_risk import GlobalRiskGate, GlobalRiskStatus, apply_global_gates
from glue.portfolio_manager import PortfolioManager
from theses_rules.rules_based_theses import buy_call_signal, hold_signal


class _FakeGateway:
    def __init__(self, nlv=100_000, cash=60_000):
        self.net_liquidation = nlv
        self.cash_value = cash
        self._option_positions = {}

    def get_cached_portfolio(self):
        return []


def test_drawdown_blocks_new_premium():
    gate = GlobalRiskGate()
    gate.reset_peak(100_000)
    gw = _FakeGateway(nlv=91_000, cash=30_000)
    status = gate.evaluate(gw)
    assert status.drawdown_gate_active is True
    assert status.block_new_premium is True

    signals = [
        ("t1", buy_call_signal(symbol="SPY", expiration="20260620", strike=580, quantity=1, limit_price=3, reason="x")),
    ]
    approved, blocked = apply_global_gates(signals, status)
    assert len(approved) == 0
    assert len(blocked) == 1


def test_premium_exposure_cap():
    gate = GlobalRiskGate()
    gate.reset_peak(100_000)
    gw = _FakeGateway(nlv=100_000, cash=50_000)
    gw._option_positions = {
        "SPY_20260620_C_580": {"qty": 10, "avg_cost": 5000.0},
    }
    status = gate.evaluate(gw)
    assert status.premium_exposure_pct >= 50.0
    assert status.block_new_premium is True


def test_portfolio_manager_aggregate_exits_first():
    mgr = PortfolioManager([])
    exit_sig = buy_call_signal(symbol="SPY", expiration="20260620", strike=580, quantity=1, limit_price=3, reason="x")
    exit_sig["action"] = "sell"
    exit_sig["option_contract"] = {"strategy": "close_option", "expiration": "20260620", "right": "C", "strike": 580}
    buy = buy_call_signal(symbol="SPY", expiration="20260720", strike=580, quantity=1, limit_price=3, reason="y")
    agg = mgr.aggregate([("a", buy), ("b", exit_sig)])
    assert agg[0][0] == "b"


def test_hold_signals_not_actionable():
    mgr = PortfolioManager([])
    plan_raw = [("t", hold_signal("wait"))]
    agg = mgr.aggregate(plan_raw)
    assert agg == []
