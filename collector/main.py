"""BTC Bottom Radar — daily collector entrypoint.

Run:  python -m collector.main            # collect, persist, alert-on-change
      python -m collector.main --digest   # also always send a status digest

Never raises on a missing optional source: degraded indicators are marked
available=False and excluded from the score denominator.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os

from . import scoring
from . import top_radar
from . import ladder as ladder_engine
from .config import env, load_thresholds, setup_logging
from .datasources import (
    fetch_daily_closes,
    fetch_fear_greed,
    fetch_weekly_closes,
)
from .indicators import ALL as INDICATORS
from .indicators.base import Context
from .indicators.onchain import OnchainProvider
from . import notify_telegram as tg
from . import persist_supabase as db
from . import ta

log = setup_logging()


def build_context(cfg: dict) -> tuple[Context, dict]:
    """Fetch all data and assemble the analysis context + a raw payload."""
    price_source = env("PRICE_SOURCE", "kraken")
    onchain_provider = env("ONCHAIN_PROVIDER", "none")
    glassnode_key = env("GLASSNODE_API_KEY") or None

    daily_closes, daily_src = fetch_daily_closes(price_source)
    weekly_closes, weekly_src = fetch_weekly_closes(price_source)
    fear_greed = fetch_fear_greed()
    onchain = OnchainProvider(onchain_provider, glassnode_key).fetch()

    price_usd = daily_closes[-1] if daily_closes else None

    # ATH: max of meta override and observed max close.
    meta_ath = cfg["meta"].get("all_time_high_usd")
    observed_ath = ta.max_close(daily_closes)
    ath_candidates = [v for v in (meta_ath, observed_ath) if v is not None]
    ath_usd = max(ath_candidates) if ath_candidates else None

    rsi_daily = ta.rsi(daily_closes, 14) if daily_closes else None
    rsi_weekly = ta.rsi(weekly_closes, 14) if weekly_closes else None

    ctx = Context(
        cfg=cfg,
        daily_closes=daily_closes,
        weekly_closes=weekly_closes,
        price_usd=price_usd,
        ath_usd=ath_usd,
        rsi_14d_daily=rsi_daily,
        rsi_14_weekly=rsi_weekly,
        fear_greed=fear_greed,
        onchain=onchain,
    )
    raw = {
        "sources": {
            "daily": daily_src,
            "weekly": weekly_src,
            "onchain_provider": onchain_provider,
        },
        "counts": {"daily_points": len(daily_closes), "weekly_points": len(weekly_closes)},
        "fear_greed": fear_greed,
        "onchain": onchain,
        "price_usd": price_usd,
        "ath_usd": ath_usd,
    }
    return ctx, raw


def build_row(ctx: Context, results: list, score: dict, raw: dict,
              top_results: list, top_score: dict) -> dict:
    """Assemble the btc.indicators row from indicator results + bottom + top score."""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    by_key = {r.key: r for r in results}
    top_by_key = {r.key: r for r in top_results}

    def val(key):
        r = by_key.get(key)
        return r.value if r else None

    def detail(key, field):
        r = by_key.get(key)
        return (r.detail.get(field) if r else None)

    def top_detail(key, field):
        r = top_by_key.get(key)
        return (r.detail.get(field) if r else None)

    row = {
        "captured_date": today,
        "price_usd": ctx.price_usd,
        "all_time_high_usd": ctx.ath_usd,
        "drawdown_from_ath_pct": val("drawdown_from_ath_pct"),
        "ema_150d": detail("pi_cycle_bottom", "ema_150d"),
        "sma_471d": detail("pi_cycle_bottom", "sma_471d"),
        "sma_200d": detail("mayer_multiple", "sma_200d"),
        "ma_200w": detail("ma_200w", "ma_200w"),
        "mayer_multiple": val("mayer_multiple"),
        "rsi_14d": val("rsi_14d"),
        "pi_cycle_bottom": bool(by_key["pi_cycle_bottom"].triggered)
        if "pi_cycle_bottom" in by_key else None,
        "fear_greed": val("fear_greed"),
        "mvrv_zscore": val("mvrv_zscore"),
        "sopr": val("sopr"),
        "supply_profit_pct": val("supply_profit_pct"),
        "triggered_count": score["triggered_count"],
        "available_count": score["available_count"],
        "bottom_score": score["bottom_score"],
        "tier": score["tier"],
        "signals_triggered": score["signals_triggered"],
        # top-radar columns
        "rsi_14w": ctx.rsi_14_weekly,
        "pi_cycle_top": bool(top_by_key["pi_cycle_top"].triggered)
        if "pi_cycle_top" in top_by_key else None,
        "nupl": ctx.onchain.get("nupl"),
        "puell_multiple": ctx.onchain.get("puell_multiple"),
        "top_score": top_score["top_score"],
        "top_tier": top_score["top_tier"],
        "top_signals_triggered": top_score["top_signals_triggered"],
        # combined detail for the dashboard table (bottom + top indicators)
        "indicators_detail": {
            r.key: {
                "value": r.value,
                "threshold": r.threshold,
                "triggered": r.triggered,
                "available": r.available,
                "detail": r.detail,
            }
            for r in (results + top_results)
        },
        "raw": raw,
    }
    return row


def run(send_digest: bool) -> int:
    cfg = load_thresholds()

    # Telegram token sanity check (does not block collection/persistence).
    telegram_ok = tg.validate_token()
    if not telegram_ok:
        log.warning("Telegram token invalid — collection continues, alerts skipped.")

    ctx, raw = build_context(cfg)
    results = [mod.compute(ctx) for mod in INDICATORS]
    score = scoring.compute_score(results, cfg)
    score["tier_emoji"] = cfg["score_tiers"][score["tier"]]["emoji"]

    # Top / sell radar (symmetric) — reuses the same fetched data.
    top_results = top_radar.compute_top_results(ctx)
    top_score = scoring.compute_top_score(top_results, cfg)

    row = build_row(ctx, results, score, raw, top_results, top_score)

    log.info(
        "bottom=%s/%s top=%s/%s | bottom_score=%s tier=%s top_score=%s top_tier=%s price=%s",
        score["triggered_count"], score["available_count"],
        top_score["triggered_count"], top_score["available_count"],
        score["bottom_score"], score["tier"],
        top_score["top_score"], top_score["top_tier"], ctx.price_usd,
    )

    client = db.get_client()
    # Fetch the latest existing snapshot BEFORE writing today's, for change
    # detection. On the first run of a day this is yesterday (day-over-day
    # diff); on a same-day re-run it is today's own row, so nothing re-fires.
    previous = db.fetch_last_snapshot(client)
    saved = db.upsert_indicators(client, row)

    # enrich today's dict with emoji + counts for message formatting
    today_view = dict(row)
    today_view["tier_emoji"] = score["tier_emoji"]
    today_view["available_count"] = score["available_count"]
    today_view["triggered_count"] = score["triggered_count"]
    today_view["top_tier_emoji"] = cfg["top_score_tiers"][top_score["top_tier"]]["emoji"]
    today_view["top_available_count"] = top_score["available_count"]
    today_view["top_triggered_count"] = top_score["triggered_count"]

    # Buy-ladder: evaluate AFTER persist and BEFORE messaging, so the digest's
    # "Jouw ladder" reflects same-run fires and we know if a ladder event occurred.
    ladder_events = {"armed": [], "fired": [], "uptrend": False}
    try:
        ladder_events = ladder_engine.evaluate(row, client=client)
    except Exception as exc:  # noqa: BLE001 - ladder must never break the daily run
        log.error("ladder evaluation failed (non-fatal): %s", exc)

    ladder_state = ladder_engine.fetch_state(client)         # fresh, post-fire
    positions = ladder_engine.positions_summary(client, ladder_engine.load_ladder()["budget"])

    # --- Reduced-noise scheduling ---------------------------------------------
    notify = cfg.get("notify", {})
    score_threshold = notify.get("score_change_alert_threshold", 5)
    digest_weekday = notify.get("digest_weekday", 0)

    tier_changed = previous is not None and today_view.get("tier") != previous.get("tier")
    score_delta = abs((today_view.get("bottom_score") or 0) - (previous.get("bottom_score") or 0)) \
        if previous is not None else 0
    ladder_event = bool(ladder_events["armed"] or ladder_events["fired"] or ladder_events["uptrend"])
    meaningful = tier_changed or score_delta >= score_threshold or ladder_event

    is_digest_day = send_digest or (dt.datetime.now(dt.timezone.utc).weekday() == digest_weekday)

    if telegram_ok:
        # CHANGE alert only on a meaningful event (and only if not already sending
        # the full digest, which subsumes it).
        if meaningful and not is_digest_day:
            events = []
            if tier_changed:
                events.append({"type": "tier_change", "from": previous.get("tier"),
                               "to": today_view.get("tier")})
            if previous is not None and score_delta >= score_threshold:
                events.append({"type": "score_delta", "delta": score_delta,
                               "from": previous.get("bottom_score"),
                               "to": today_view.get("bottom_score")})
            if ladder_events["armed"]:
                events.append({"type": "ladder_event",
                               "text": f"trap {ladder_events['armed']} bewapend"})
            if ladder_events["fired"]:
                reason = "uptrend" if ladder_events["uptrend"] else "bevestigd"
                events.append({"type": "ladder_event",
                               "text": f"trap {sorted(set(ladder_events['fired']))} koop-signaal ({reason})"})
            msg = tg.format_alert(events, today_view, ladder_state, cfg)
            if tg.send_message(msg):
                db.insert_alert(client, {
                    "alert_type": "change", "tier": score["tier"],
                    "bottom_score": score["bottom_score"], "message": msg,
                    "payload": {"events": events}, "delivered": True})

        # Top-radar alert on top-tier change / new strong top signal (always meaningful).
        top_events = tg.detect_top_changes(today_view, previous)
        if top_events:
            tmsg = tg.format_top_alert(top_events, today_view)
            if tg.send_message(tmsg):
                db.insert_alert(client, {
                    "alert_type": "top_change", "tier": top_score["top_tier"],
                    "bottom_score": top_score["top_score"], "message": tmsg,
                    "payload": {"events": top_events}, "delivered": True})

        # FULL digest only on the weekly digest day or when forced with --digest.
        if is_digest_day:
            digest = tg.format_digest(today_view, results, top_results, cfg, ladder_state, positions)
            delivered = tg.send_message(digest)
            db.insert_alert(client, {
                "alert_type": "digest", "tier": score["tier"],
                "bottom_score": score["bottom_score"], "message": digest,
                "payload": {"signals_triggered": score["signals_triggered"],
                            "top_signals_triggered": top_score["top_signals_triggered"]},
                "delivered": delivered})
        elif not meaningful:
            log.info("quiet day — row written, no meaningful event, not digest day; nothing sent.")

    log.info("done. row id=%s date=%s", saved.get("id"), saved.get("captured_date"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BTC Bottom Radar collector")
    parser.add_argument("--digest", action="store_true",
                        help="always send a Telegram status digest")
    args = parser.parse_args()
    return run(send_digest=args.digest)


if __name__ == "__main__":
    raise SystemExit(main())
