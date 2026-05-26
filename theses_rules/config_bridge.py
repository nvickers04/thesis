"""Build :class:`RuleThesis` instances from :class:`theses.Thesis` config."""

from __future__ import annotations

from theses import Thesis
from theses_rules.rules_based_theses import (
    AutomationBoomLeveragedThesis,
    CoveredCallOverlayThesis,
    DefenseGrowthLeveragedThesis,
    LeveragedOvernightDriftThesis,
    LunarOptionsSwingThesis,
    RuleThesis,
)

_FACTORIES: dict[str, type] = {
    "overnight_drift": LeveragedOvernightDriftThesis,
    "lunar_swing": LunarOptionsSwingThesis,
    "defense_growth": DefenseGrowthLeveragedThesis,
    "automation_boom": AutomationBoomLeveragedThesis,
    "covered_call_overlay": CoveredCallOverlayThesis,
}


def thesis_to_rule(thesis: Thesis) -> RuleThesis:
    """Materialize a mechanical evaluator from thesis config."""
    cls = _FACTORIES.get(thesis.id)
    if cls is None:
        raise ValueError(f"No rule class registered for thesis id {thesis.id!r}")

    kwargs: dict = {
        "id": thesis.id,
        "name": thesis.name,
        "watchlist": list(thesis.watchlist),
        "enabled": thesis.enabled,
        "instruments": list(thesis.instruments),
        "option_strategies": list(thesis.option_strategies),
        "data_fields": list(thesis.data_fields),
        "risk_per_trade_pct": thesis.base_risk_pct,
    }

    dte_lo, dte_hi = thesis.dte_range
    delta_lo, delta_hi = thesis.delta_band

    if thesis.id == "overnight_drift":
        kwargs.update(
            symbol=thesis.watchlist[0],
            min_dte=dte_lo,
            max_dte=dte_hi,
            min_delta=delta_lo,
            max_delta=delta_hi,
            target_delta=thesis.target_delta,
            sma_period=thesis.sma_period,
            vix_max=thesis.vix_max or 30.0,
        )
    elif thesis.id == "lunar_swing":
        kwargs.update(
            min_dte=dte_lo,
            max_dte=dte_hi,
            min_delta=delta_lo,
            max_delta=delta_hi,
            target_delta=thesis.target_delta,
            take_profit_pct=thesis.take_profit_pct or 60.0,
        )
    elif thesis.id in ("defense_growth", "automation_boom"):
        spread_lo, spread_hi = thesis.spread_dte_range
        kwargs.update(
            sma_period=thesis.sma_period,
            leap_min_dte=thesis.leap_min_dte,
            leap_min_delta=delta_lo,
            leap_max_delta=delta_hi,
            leap_target_delta=thesis.target_delta,
            spread_min_dte=spread_lo,
            spread_max_dte=spread_hi,
        )
    elif thesis.id == "covered_call_overlay":
        kwargs.update(
            core_symbols=list(thesis.watchlist),
            min_dte=dte_lo,
            max_dte=dte_hi,
            min_otm_pct=thesis.min_otm_pct or 3.0,
            max_otm_pct=thesis.max_otm_pct or 5.0,
        )

    return cls(**kwargs)


def rules_from_theses(theses: list[Thesis]) -> list[RuleThesis]:
    return [thesis_to_rule(t) for t in theses if t.enabled]
