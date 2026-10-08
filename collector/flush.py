"""Flush-check — classifies a price drop as a *leverage flush* or *spot
selling* from derivatives positioning. Pure function, no I/O, unit-tested.

The question it answers: "BTC fell — were sellers forced (liquidated longs,
open interest wiped, funding reset) or did spot holders actually sell (open
interest intact, funding still positive)?" A flush historically exhausts
itself fast; spot-led selling with leverage still in the system tends to
have follow-through. This is a HEURISTIC read of positioning, calibrated
analytically (not backtested) — see config `derivatives._caveat`. It is never
a buy/sell signal.

Verdicts (NL keys used in DB, Telegram and dashboard):
  geen_daling      price did not drop enough to ask the question
  hefboom_flush    OI wiped, longs liquidated / funding reset → forced selling
  spot_verkoop     OI intact, funding still positive → real selling, leverage not yet washed
  shorts_stapelen  OI rising while funding negative → shorts piling in (squeeze risk)
  gemengd          drop with inconclusive positioning
  onbekend         too little data to say
"""
from __future__ import annotations

VERDICT_EMOJI = {
    "geen_daling": "⚪", "hefboom_flush": "🧹", "spot_verkoop": "🩸",
    "shorts_stapelen": "🐻", "gemengd": "🌫️", "onbekend": "❔",
}
VERDICT_NL = {
    "geen_daling": "geen noemenswaardige daling",
    "hefboom_flush": "hefboom-flush (gedwongen verkoop)",
    "spot_verkoop": "spot-verkoop (echte verkoopdruk)",
    "shorts_stapelen": "shorts stapelen zich op",
    "gemengd": "gemengd beeld",
    "onbekend": "onvoldoende data",
}
VERDICT_MEANING_NL = {
    "geen_daling": "Koers zakte niet genoeg om de vraag te stellen.",
    "hefboom_flush": ("Open interest werd weggeveegd en longs geliquideerd: de verkopers waren "
                      "gedwongen, niet overtuigd. Zo'n flush put zichzelf meestal snel uit — "
                      "maar zegt niets over de trend erna."),
    "spot_verkoop": ("Open interest bleef intact en funding is nog positief: dit was spot-verkoop "
                     "terwijl de hefboom nog in het systeem zit. Risico op vervolg-daling blijft."),
    "shorts_stapelen": ("Open interest stijgt bij negatieve funding: nieuwe shorts openen in de "
                        "daling. Dat is brandstof voor een short-squeeze, geen bodembewijs."),
    "gemengd": "Positionering spreekt elkaar tegen; geen duidelijke flush noch pure spot-verkoop.",
    "onbekend": "Derivatives-data ontbrak (zie 'missing'); oordeel niet mogelijk.",
}


def price_change_pct(daily_closes: list[float]) -> float | None:
    """Last close vs the previous daily close (Kraken's last candle is today's
    partial, so this reads 'now vs yesterday's close')."""
    if len(daily_closes) < 2 or not daily_closes[-2]:
        return None
    return round(100 * (daily_closes[-1] - daily_closes[-2]) / daily_closes[-2], 2)


def classify(price_chg_pct: float | None, snap: dict, cfg: dict) -> dict:
    """Return {verdict, emoji, label_nl, meaning_nl, reasons[], inputs{}}."""
    d = cfg.get("derivatives", {})
    drop = float(d.get("price_drop_pct", 2.0))
    oi_flush = float(d.get("oi_flush_drop_pct", 4.0))
    oi_band = float(d.get("oi_stable_band_pct", 1.5))
    f_neg = float(d.get("funding_negative", 0.0))
    f_hot = float(d.get("funding_hot", 0.0001))
    long_dom = float(d.get("long_liq_dominance_pct", 65))

    oi_chg = snap.get("oi_change_24h_pct")
    funding = snap.get("funding_rate")
    long_share = snap.get("liq_long_share_pct")
    liq_total = (snap.get("liq_long_usd") or 0) + (snap.get("liq_short_usd") or 0)
    inputs = {"price_chg_24h_pct": price_chg_pct, "oi_change_24h_pct": oi_chg,
              "funding_rate": funding, "liq_long_share_pct": long_share,
              "liq_total_usd": liq_total if snap.get("liq_long_usd") is not None else None,
              "liq_covered_hours": snap.get("liq_covered_hours"),
              "liq_truncated": snap.get("liq_truncated")}
    reasons: list[str] = []

    def out(verdict: str) -> dict:
        return {"verdict": verdict, "emoji": VERDICT_EMOJI[verdict],
                "label_nl": VERDICT_NL[verdict], "meaning_nl": VERDICT_MEANING_NL[verdict],
                "reasons": reasons, "inputs": inputs}

    if price_chg_pct is None:
        reasons.append("geen koersverandering beschikbaar")
        return out("onbekend")
    if price_chg_pct > -drop:
        reasons.append(f"koers {price_chg_pct:+.1f}% (drempel −{drop:.0f}%)")
        return out("geen_daling")
    reasons.append(f"koers {price_chg_pct:+.1f}% vs vorig dagslot")

    if oi_chg is None and funding is None:
        reasons.append("open interest én funding ontbreken")
        return out("onbekend")

    oi_wiped = oi_chg is not None and oi_chg <= -oi_flush
    oi_stable_or_up = oi_chg is not None and oi_chg >= -oi_band
    oi_up = oi_chg is not None and oi_chg > oi_band
    funding_cool = funding is not None and funding <= f_hot
    funding_negative = funding is not None and funding <= f_neg
    funding_positive = funding is not None and funding > f_neg
    longs_dominated = long_share is not None and liq_total > 0 and long_share >= long_dom

    if oi_chg is not None:
        reasons.append(f"open interest {oi_chg:+.1f}% in 24u")
    if funding is not None:
        reasons.append(f"funding {funding * 100:+.4f}%/8u")
    if long_share is not None and liq_total > 0:
        reasons.append(f"liquidaties {long_share:.0f}% longs (${liq_total / 1e6:.1f}M, "
                       f"{snap.get('liq_covered_hours')}u gezien)")

    if oi_wiped and (funding_cool or longs_dominated or long_share is None):
        return out("hefboom_flush")
    if oi_up and funding_negative:
        return out("shorts_stapelen")
    if oi_stable_or_up and funding_positive:
        return out("spot_verkoop")
    return out("gemengd")
