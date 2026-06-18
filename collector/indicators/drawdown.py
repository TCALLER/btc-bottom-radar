"""Drawdown from ATH. ATH = max(closes) but honor meta.all_time_high_usd override.
Triggered when |drawdown| >= bottom_value (75%)."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "drawdown_from_ath_pct"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    available = ctx.price_usd is not None and ctx.ath_usd is not None and ctx.ath_usd > 0
    dd_pct = None
    if available:
        # positive percentage below ATH
        dd_pct = (1.0 - (ctx.price_usd / ctx.ath_usd)) * 100.0
    triggered = bool(dd_pct is not None and dd_pct >= bottom)
    return IndicatorResult(
        key=KEY,
        value=dd_pct,
        threshold=f">= {bottom}%",
        triggered=triggered,
        available=available,
        detail={
            "drawdown_from_ath_pct": dd_pct,
            "all_time_high_usd": ctx.ath_usd,
            "price_usd": ctx.price_usd,
            "weight": cfg["weight"],
        },
    )
