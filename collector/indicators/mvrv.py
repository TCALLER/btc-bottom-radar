"""MVRV Z-Score (optional on-chain). Triggered <= bottom_value (0.1), watch <= 0.5.
available=False when the on-chain provider returns no value (weight excluded)."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "mvrv_zscore"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    val = ctx.onchain.get("mvrv_zscore")
    available = val is not None
    triggered = bool(available and val <= bottom)
    return IndicatorResult(
        key=KEY,
        value=val,
        threshold=f"<= {bottom}",
        triggered=triggered,
        available=available,
        detail={"mvrv_zscore": val, "watch_value": cfg.get("watch_value"), "weight": cfg["weight"]},
    )
