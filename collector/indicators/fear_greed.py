"""Fear & Greed index (alternative.me). Triggered <= bottom_value (10)."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "fear_greed"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    fg = ctx.fear_greed
    available = fg is not None
    triggered = bool(available and fg <= bottom)
    return IndicatorResult(
        key=KEY,
        value=fg,
        threshold=f"<= {bottom}",
        triggered=triggered,
        available=available,
        detail={"fear_greed": fg, "watch_value": cfg.get("watch_value"), "weight": cfg["weight"]},
    )
