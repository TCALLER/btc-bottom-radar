"""Shared indicator types and the analysis context."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IndicatorResult:
    key: str
    value: float | bool | int | None
    threshold: Any
    triggered: bool
    available: bool
    detail: dict = field(default_factory=dict)


@dataclass
class Context:
    """Everything the indicators need, fetched once per run."""
    cfg: dict
    daily_closes: list[float]
    weekly_closes: list[float]
    price_usd: float | None
    ath_usd: float | None
    rsi_14d_daily: float | None
    rsi_14_weekly: float | None
    fear_greed: int | None
    onchain: dict

    def icfg(self, key: str) -> dict:
        return self.cfg["indicators"][key]
