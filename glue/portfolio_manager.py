"""
Aggregate mechanical thesis signals into a single portfolio execution plan.

Priority: exits → risk-reducing sells → new entries (deduped by symbol).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from glue.global_risk import GlobalRiskGate, GlobalRiskStatus, apply_global_gates
from theses_rules.rules_based_theses import (
    RuleThesis,
    RulesContext,
    TradeSignal,
    hold_signal,
    signal_to_execution_decision,
)

logger = logging.getLogger(__name__)

# Lower = higher priority
_ACTION_PRIORITY = {
    "close_short_call": 0,
    "close_option": 1,
    "covered_call": 2,
    "vertical_spread": 4,
    "long_call": 5,
    "long_put": 5,
}


def _signal_priority(signal: TradeSignal) -> tuple[int, int, str]:
    action = str(signal.get("action", "hold")).lower()
    opt = signal.get("option_contract") or {}
    strategy = str(opt.get("strategy", "")).lower()
    if action == "hold":
        return (99, 99, "")
    if strategy in ("close_option", "close_short_call"):
        pri = _ACTION_PRIORITY.get(strategy, 1)
    elif action == "sell":
        pri = _ACTION_PRIORITY.get(strategy, 2)
    else:
        pri = _ACTION_PRIORITY.get(strategy, 5)
    return (pri, 0 if action in ("sell", "buy") else 1, signal.get("symbol", ""))


def _dedupe_key(thesis_id: str, signal: TradeSignal) -> tuple[str, str, str, str]:
    action = str(signal.get("action", "hold")).lower()
    sym = str(signal.get("symbol", "")).upper()
    opt = signal.get("option_contract") or {}
    strategy = str(opt.get("strategy", "")).lower()
    exp = str(opt.get("expiration", ""))
    return (thesis_id, sym, action, f"{strategy}:{exp}")


@dataclass
class PortfolioPlan:
    """Aggregated execution plan for one rules cycle."""

    raw_signals: list[tuple[str, TradeSignal]] = field(default_factory=list)
    approved_signals: list[tuple[str, TradeSignal]] = field(default_factory=list)
    blocked_signals: list[tuple[str, TradeSignal, str]] = field(default_factory=list)
    global_risk: GlobalRiskStatus | None = None

    @property
    def actionable(self) -> list[tuple[str, TradeSignal]]:
        return [
            (tid, sig)
            for tid, sig in self.approved_signals
            if str(sig.get("action", "hold")).lower() != "hold"
        ]


class PortfolioManager:
    """Collect, aggregate, and gate signals from all enabled rule theses."""

    def __init__(
        self,
        theses: list[RuleThesis],
        *,
        global_risk: GlobalRiskGate | None = None,
    ) -> None:
        self.theses = theses
        self.global_risk = global_risk or GlobalRiskGate()

    def collect_signals(self, ctx: RulesContext) -> list[tuple[str, TradeSignal]]:
        out: list[tuple[str, TradeSignal]] = []
        for thesis in self.theses:
            try:
                sig = thesis.evaluate(ctx)
            except Exception as exc:
                logger.warning("Thesis %s evaluate failed: %s", thesis.id, exc)
                sig = hold_signal(f"evaluate error: {exc}", symbol=thesis.watchlist[0] if thesis.watchlist else "")
            out.append((thesis.id, sig))
        return out

    def aggregate(self, signals: list[tuple[str, TradeSignal]]) -> list[tuple[str, TradeSignal]]:
        """Sort by exit-first priority; drop duplicate hold-equivalent keys."""
        non_hold = [
            (tid, sig)
            for tid, sig in signals
            if str(sig.get("action", "hold")).lower() != "hold"
        ]
        non_hold.sort(key=lambda x: _signal_priority(x[1]))

        seen: set[tuple[str, str, str, str]] = set()
        deduped: list[tuple[str, TradeSignal]] = []
        for tid, sig in non_hold:
            key = _dedupe_key(tid, sig)
            sym_action = (key[1], key[2], key[3])
            if sym_action in seen:
                continue
            seen.add(sym_action)
            deduped.append((tid, sig))
        return deduped

    def build_plan(
        self,
        ctx: RulesContext,
        gateway: Any,
        *,
        data_provider: Any | None = None,
    ) -> PortfolioPlan:
        raw = self.collect_signals(ctx)
        aggregated = self.aggregate(raw)
        risk_status = self.global_risk.evaluate(gateway, data_provider=data_provider or ctx.provider)
        approved, blocked = apply_global_gates(aggregated, risk_status)
        return PortfolioPlan(
            raw_signals=raw,
            approved_signals=approved,
            blocked_signals=blocked,
            global_risk=risk_status,
        )

    def signal_to_decision(self, thesis_id: str, signal: TradeSignal) -> dict[str, Any]:
        decision = signal_to_execution_decision(signal)
        decision["thesis_id"] = thesis_id
        return decision
