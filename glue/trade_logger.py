"""
Transparent trade + decision logging.

Every cycle writes a JSON line to ``logs/thesis_trader.jsonl`` so you can
audit what Grok said, what risk approved, and what executed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from glue.bootstrap import logs_dir

logger = logging.getLogger(__name__)


class TradeLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (logs_dir() / "thesis_trader.jsonl")

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            **payload,
        }
        line = json.dumps(record, default=str)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        logger.info("[%s] %s", event_type, json.dumps(payload, default=str)[:500])

    def log_cycle_start(self, thesis_id: str, watchlist: list[str]) -> None:
        self.log_event("cycle_start", {"thesis_id": thesis_id, "watchlist": watchlist})

    def log_market_data(self, thesis_id: str, data: dict[str, Any]) -> None:
        self.log_event("market_data", {"thesis_id": thesis_id, "data": data})

    def log_grok_raw(self, thesis_id: str, raw: str) -> None:
        self.log_event("grok_raw", {"thesis_id": thesis_id, "raw": raw})

    def log_decision(self, thesis_id: str, decision: dict[str, Any]) -> None:
        self.log_event("grok_decision", {"thesis_id": thesis_id, "decision": decision})

    def log_rules_decision(self, thesis_id: str, decision: dict[str, Any]) -> None:
        self.log_event("rules_decision", {"thesis_id": thesis_id, "decision": decision})

    def log_risk(self, thesis_id: str, verdict: Any) -> None:
        self.log_event(
            "risk_verdict",
            {
                "thesis_id": thesis_id,
                "approved": verdict.approved,
                "reason": verdict.reason,
                "adjusted_quantity": verdict.adjusted_quantity,
            },
        )

    def log_execution(self, thesis_id: str, result: dict[str, Any]) -> None:
        self.log_event("execution", {"thesis_id": thesis_id, "result": result})
