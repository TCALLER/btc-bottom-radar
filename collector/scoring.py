"""Composite bottom score over AVAILABLE indicator weights.

score = round(100 * sum(weight where available and triggered)
                  / sum(weight where available))
Optional indicators that are unavailable are excluded from the denominator so
the free price/sentiment core scores fairly on its own."""
from __future__ import annotations

from .indicators.base import IndicatorResult


def compute_score(results: list[IndicatorResult], cfg: dict) -> dict:
    indicators_cfg = cfg["indicators"]
    possible = 0
    earned = 0
    available_count = 0
    triggered_count = 0
    triggered_keys: list[str] = []

    for r in results:
        weight = indicators_cfg[r.key]["weight"]
        if r.available:
            possible += weight
            available_count += 1
            if r.triggered:
                earned += weight
                triggered_count += 1
                triggered_keys.append(r.key)

    score = round(100 * earned / possible) if possible > 0 else 0
    tier = _tier_for(score, cfg["score_tiers"])

    return {
        "bottom_score": score,
        "tier": tier,
        "tier_emoji": cfg["score_tiers"][tier]["emoji"],
        "available_count": available_count,
        "triggered_count": triggered_count,
        "signals_triggered": triggered_keys,
        "possible_weight": possible,
        "earned_weight": earned,
    }


def _tier_for(score: int, tiers: dict) -> str:
    for name, band in tiers.items():
        if band["min"] <= score <= band["max"]:
            return name
    return "neutraal"
