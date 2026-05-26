"""
Portfolio-level risk gates for rules mode.

- Peak drawdown >= 8% → block new premium risk; target 50% cash allocation.
- Total option premium exposure capped at 50% of net liquidation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from theses_rules.rules_based_theses import TradeSignal


@dataclass(frozen=True)
class GlobalRiskStatus:
    net_liquidation: float
    cash: float
    cash_pct: float
    peak_nlv: float
    drawdown_pct: float
    premium_exposure: float
    premium_exposure_pct: float
    drawdown_gate_active: bool
    premium_gate_active: bool
    block_new_premium: bool
    reason: str = ""


@dataclass
class GlobalRiskGate:
    """Configurable portfolio gates (defaults match thesis spec)."""

    drawdown_halt_pct: float = 8.0
    drawdown_cash_target_pct: float = 50.0
    max_premium_exposure_pct: float = 50.0

    _peak_nlv: float = field(default=0.0, init=False, repr=False)

    def update_peak(self, nlv: float) -> float:
        if nlv > self._peak_nlv:
            self._peak_nlv = nlv
        return self._peak_nlv

    def reset_peak(self, nlv: float) -> None:
        self._peak_nlv = max(0.0, nlv)

    @property
    def peak_nlv(self) -> float:
        return self._peak_nlv

    @staticmethod
    def drawdown_pct(nlv: float, peak: float) -> float:
        if peak <= 0 or nlv <= 0:
            return 0.0
        return max(0.0, (peak - nlv) / peak * 100.0)

    @staticmethod
    def estimate_premium_exposure(gateway: Any, data_provider: Any | None = None) -> float:
        """Sum absolute option market value (long + short legs)."""
        total = 0.0
        internal = getattr(gateway, "_option_positions", None)
        if isinstance(internal, dict):
            for key, pos in internal.items():
                qty = int(pos.get("qty", 0) or 0)
                if qty == 0:
                    continue
                parts = str(key).split("_")
                if len(parts) < 4:
                    continue
                sym, exp, right, strike_s = parts[0], parts[1], parts[2], parts[3]
                avg_cost = float(pos.get("avg_cost", 0) or 0)
                if avg_cost > 0:
                    # PaperBroker stores avg_cost as premium * 100 (dollars per contract)
                    total += abs(avg_cost * abs(qty))
                elif data_provider is not None and hasattr(gateway, "_option_premium"):
                    premium = float(gateway._option_premium(sym, exp, float(strike_s), right))
                    total += abs(premium * 100.0 * abs(qty))
            return total

        for item in getattr(gateway, "get_cached_portfolio", lambda: [])() or []:
            contract = getattr(item, "contract", item)
            sec_type = str(getattr(contract, "secType", getattr(item, "sec_type", "STK"))).upper()
            if sec_type not in ("OPT", "OPTION"):
                continue
            total += abs(float(getattr(item, "marketValue", 0) or 0))
        return total

    def evaluate(self, gateway: Any, *, data_provider: Any | None = None) -> GlobalRiskStatus:
        nlv = float(getattr(gateway, "net_liquidation", 0) or 0)
        cash = float(getattr(gateway, "cash_value", 0) or 0)
        peak = self.update_peak(nlv)
        dd = self.drawdown_pct(nlv, peak)
        cash_pct = (cash / nlv * 100.0) if nlv > 0 else 100.0
        premium = self.estimate_premium_exposure(gateway, data_provider)
        prem_pct = (premium / nlv * 100.0) if nlv > 0 else 0.0

        dd_active = dd >= self.drawdown_halt_pct
        prem_active = prem_pct >= self.max_premium_exposure_pct
        block = False
        reasons: list[str] = []

        if dd_active and cash_pct < self.drawdown_cash_target_pct:
            block = True
            reasons.append(
                f"drawdown {dd:.1f}% >= {self.drawdown_halt_pct:.0f}% "
                f"and cash {cash_pct:.1f}% < {self.drawdown_cash_target_pct:.0f}% target"
            )
        if prem_active:
            block = True
            reasons.append(
                f"premium exposure {prem_pct:.1f}% >= {self.max_premium_exposure_pct:.0f}% cap"
            )

        return GlobalRiskStatus(
            net_liquidation=nlv,
            cash=cash,
            cash_pct=round(cash_pct, 2),
            peak_nlv=peak,
            drawdown_pct=round(dd, 2),
            premium_exposure=round(premium, 2),
            premium_exposure_pct=round(prem_pct, 2),
            drawdown_gate_active=dd_active,
            premium_gate_active=prem_active,
            block_new_premium=block,
            reason="; ".join(reasons),
        )


def _is_exit_signal(signal: TradeSignal) -> bool:
    action = str(signal.get("action", "hold")).lower()
    if action == "hold":
        return False
    opt = signal.get("option_contract") or {}
    strategy = str(opt.get("strategy", "")).lower()
    if strategy in ("close_option", "close_short_call"):
        return True
    if action == "sell" and strategy == "covered_call":
        return False
    if action == "sell":
        return True
    return False


def _expands_premium_risk(signal: TradeSignal) -> bool:
    action = str(signal.get("action", "hold")).lower()
    if action != "buy":
        return False
    opt = signal.get("option_contract") or {}
    strategy = str(opt.get("strategy", "")).lower()
    return strategy in ("long_call", "long_put", "vertical_spread", "")


def apply_global_gates(
    signals: list[tuple[str, TradeSignal]],
    status: GlobalRiskStatus,
) -> tuple[list[tuple[str, TradeSignal]], list[tuple[str, TradeSignal, str]]]:
    """
    Filter aggregated signals through global gates.

    Returns:
        (approved, blocked) where blocked entries include a reason string.
    """
    if not status.block_new_premium:
        return signals, []

    approved: list[tuple[str, TradeSignal]] = []
    blocked: list[tuple[str, TradeSignal, str]] = []
    for thesis_id, sig in signals:
        if _is_exit_signal(sig) or not _expands_premium_risk(sig):
            approved.append((thesis_id, sig))
        else:
            blocked.append((thesis_id, sig, status.reason or "global risk gate"))
    return approved, blocked
