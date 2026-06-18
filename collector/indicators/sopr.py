"""SOPR (adjusted), optional on-chain. Triggered < bottom_value (1.0).

The 'sustained ~14d' rule requires a history we do not persist intraday; with a
single daily reading we trigger on the spot value < 1.0 and record that the
sustained-days check is approximated. available=False when no provider value."""
from __future__ import annotations

from .base import Context, IndicatorResult

KEY = "sopr"


def compute(ctx: Context) -> IndicatorResult:
    cfg = ctx.icfg(KEY)
    bottom = cfg["bottom_value"]
    val = ctx.onchain.get("sopr")
    available = val is not None
    triggered = bool(available and val < bottom)
    return IndicatorResult(
        key=KEY,
        value=val,
        threshold=f"< {bottom}",
        triggered=triggered,
        available=available,
        detail={
            "sopr": val,
            "sustained_days_target": cfg.get("sustained_days"),
            "sustained_check": "spot",
            "weight": cfg["weight"],
        },
    )
