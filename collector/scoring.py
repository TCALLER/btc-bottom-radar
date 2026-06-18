"""Composite bottom score over AVAILABLE indicator weights.

score = round(100 * sum(weight where available and triggered)
                  / sum(weight where available))
Optional indicators that are unavailable are excluded from the denominator so
the free price/sentiment core scores fairly on its own."""
from __future__ import annotations

from .indicators.base import IndicatorResult


def _score(results: list[IndicatorResult], indicators_cfg: dict, tiers: dict) -> dict:
    """Generic weighted-normalized score over AVAILABLE weights. Used for both
    the bottom radar and the symmetric top radar (same math, different config)."""
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
    tier = _tier_for(score, tiers)
    return {
        "score": score,
        "tier": tier,
        "tier_emoji": tiers[tier]["emoji"],
        "available_count": available_count,
        "triggered_count": triggered_count,
        "signals_triggered": triggered_keys,
        "possible_weight": possible,
        "earned_weight": earned,
    }


def compute_score(results: list[IndicatorResult], cfg: dict) -> dict:
    s = _score(results, cfg["indicators"], cfg["score_tiers"])
    return {
        "bottom_score": s["score"],
        "tier": s["tier"],
        "tier_emoji": s["tier_emoji"],
        "available_count": s["available_count"],
        "triggered_count": s["triggered_count"],
        "signals_triggered": s["signals_triggered"],
        "possible_weight": s["possible_weight"],
        "earned_weight": s["earned_weight"],
    }


def compute_top_score(results: list[IndicatorResult], cfg: dict) -> dict:
    s = _score(results, cfg["top_indicators"], cfg["top_score_tiers"])
    return {
        "top_score": s["score"],
        "top_tier": s["tier"],
        "top_tier_emoji": s["tier_emoji"],
        "available_count": s["available_count"],
        "triggered_count": s["triggered_count"],
        "top_signals_triggered": s["signals_triggered"],
        "possible_weight": s["possible_weight"],
        "earned_weight": s["earned_weight"],
    }


def _tier_for(score: int, tiers: dict) -> str:
    for name, band in tiers.items():
        if band["min"] <= score <= band["max"]:
            return name
    return "neutraal"
