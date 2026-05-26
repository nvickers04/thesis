"""Mechanical rule theses — driven by ``theses.py`` via config_bridge."""

from theses_rules.config_bridge import rules_from_theses, thesis_to_rule
from theses_rules.rules_based_theses import (
    AutomationBoomLeveragedThesis,
    CoveredCallOverlayThesis,
    DefenseGrowthLeveragedThesis,
    LeveragedOvernightDriftThesis,
    LunarOptionsSwingThesis,
    RULES_THESES,
    RuleThesis,
    RulesContext,
    TradeSignal,
    active_rules_theses,
    hold_signal,
    signal_to_execution_decision,
)

__all__ = [
    "AutomationBoomLeveragedThesis",
    "CoveredCallOverlayThesis",
    "DefenseGrowthLeveragedThesis",
    "LeveragedOvernightDriftThesis",
    "LunarOptionsSwingThesis",
    "RULES_THESES",
    "RuleThesis",
    "RulesContext",
    "TradeSignal",
    "active_rules_theses",
    "hold_signal",
    "rules_from_theses",
    "signal_to_execution_decision",
    "thesis_to_rule",
]
