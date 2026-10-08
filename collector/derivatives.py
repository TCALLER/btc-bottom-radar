"""Derivatives snapshot — funding rate, open interest (now + 24h ago) and
recent forced liquidations for BTC perpetuals. Feeds the flush-check
(collector/flush.py) that answers: "was this drop a leverage flush or real
spot selling?"

Sources (free, no API key, reachable from GitHub-hosted runners):
  * OKX public API  — primary. Funding, live OI, hourly OI history, and the
    public liquidation-orders feed (paginated, 100 per page).
  * Bybit public API — fallback for funding + OI. Bybit publishes no REST
    liquidation feed, so liquidations stay unavailable on the fallback path.
Binance Futures is deliberately NOT used: it returns HTTP 451 for US IPs,
which is where GitHub-hosted runners live.

Every fetch has timeout + retry and degrades to None; a dead source never
breaks the daily run. Nothing here is a trade signal.

Run standalone (prints the snapshot, no DB write):
    python -m collector.derivatives
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from .datasources import _get_json

log = logging.getLogger("btc-bottom-radar.derivatives")

_OKX = "https://www.okx.com/api/v5"
_BYBIT = "https://api.bybit.com/v5"
_OKX_SWAP = "BTC-USDT-SWAP"
_OKX_ULY = "BTC-USDT"
_HOUR_MS = 3_600_000
_DAY_MS = 24 * _HOUR_MS


def _f(v) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# OKX
# ---------------------------------------------------------------------------

def _okx_ok(data) -> list | None:
    if isinstance(data, dict) and data.get("code") == "0" and isinstance(data.get("data"), list):
        return data["data"]
    return None


def okx_funding() -> dict | None:
    """Current funding rate (per 8h period) + premium (perp vs index, fraction)."""
    rows = _okx_ok(_get_json(f"{_OKX}/public/funding-rate", {"instId": _OKX_SWAP}))
    if not rows:
        return None
    r = rows[0]
    return {
        "funding_rate": _f(r.get("fundingRate")),
        "premium": _f(r.get("premium")),
        "funding_time_ms": _f(r.get("fundingTime")),
    }


def okx_open_interest_now() -> dict | None:
    rows = _okx_ok(_get_json(f"{_OKX}/public/open-interest",
                             {"instType": "SWAP", "instId": _OKX_SWAP}))
    if not rows:
        return None
    r = rows[0]
    return {"oi_btc": _f(r.get("oiCcy")), "oi_usd": _f(r.get("oiUsd")), "ts_ms": _f(r.get("ts"))}


def okx_open_interest_24h_ago(now_ms: int) -> dict | None:
    """Hourly OI history (rubik stats, newest first, [ts, oi_usd, vol_usd]).
    Returns the point closest to now-24h, with its actual age so the caller can
    judge how faithful the "24h change" is (the rubik series can lag)."""
    rows = _okx_ok(_get_json(f"{_OKX}/rubik/stat/contracts/open-interest-volume",
                             {"ccy": "BTC", "period": "1H"}))
    if not rows:
        return None
    target = now_ms - _DAY_MS
    best = None
    for row in rows:
        try:
            ts, oi_usd = int(row[0]), float(row[1])
        except (IndexError, TypeError, ValueError):
            continue
        dist = abs(ts - target)
        if best is None or dist < best[0]:
            best = (dist, ts, oi_usd)
    if best is None:
        return None
    _, ts, oi_usd = best
    return {"oi_usd": oi_usd, "ts_ms": ts, "age_hours": round((now_ms - ts) / _HOUR_MS, 1)}


def okx_liquidations_24h(now_ms: int, ct_val_btc: float, max_pages: int) -> dict | None:
    """Sum forced liquidations over the trailing 24h, split long vs short.
    OKX pages 100 orders at a time (`after` = ts cursor, newest first). On a
    violent day 24h can exceed max_pages × 100 orders — then `truncated=True`
    and `covered_hours` says how much of the window was actually seen. Never
    extrapolated."""
    cutoff = now_ms - _DAY_MS
    long_usd = short_usd = 0.0
    long_n = short_n = 0
    oldest_ts: int | None = None
    newest_ts: int | None = None
    after: int | None = None
    pages = 0
    truncated = False
    while pages < max_pages:
        params = {"instType": "SWAP", "uly": _OKX_ULY, "state": "filled", "limit": 100}
        if after is not None:
            params["after"] = after
        rows = _okx_ok(_get_json(f"{_OKX}/public/liquidation-orders", params))
        pages += 1
        if rows is None:
            if pages == 1:
                return None          # source down → unavailable, not zero
            break
        details = []
        for group in rows:
            details.extend(group.get("details") or [])
        if not details:
            break
        reached_cutoff = False
        for d in details:
            ts = int(_f(d.get("ts")) or 0)
            if ts < cutoff:
                reached_cutoff = True
                continue
            px, sz = _f(d.get("bkPx")), _f(d.get("sz"))
            if px is None or sz is None:
                continue
            usd = sz * ct_val_btc * px
            if d.get("posSide") == "long":
                long_usd += usd; long_n += 1
            elif d.get("posSide") == "short":
                short_usd += usd; short_n += 1
            newest_ts = ts if newest_ts is None else max(newest_ts, ts)
            oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
        if reached_cutoff:
            break
        after = min(int(_f(d.get("ts")) or 0) for d in details)
        time.sleep(0.45)             # OKX public limit: 5 req / 2 s
    else:
        truncated = True
    covered_hours = round((now_ms - oldest_ts) / _HOUR_MS, 1) if oldest_ts else 0.0
    total = long_usd + short_usd
    return {
        "liq_long_usd": round(long_usd, 2), "liq_short_usd": round(short_usd, 2),
        "liq_long_count": long_n, "liq_short_count": short_n,
        "liq_long_share_pct": round(100 * long_usd / total, 1) if total > 0 else None,
        "covered_hours": min(covered_hours, 24.0), "truncated": truncated, "pages": pages,
        "newest_ts_ms": newest_ts,
    }


# ---------------------------------------------------------------------------
# Bybit fallback (funding + OI only)
# ---------------------------------------------------------------------------

def bybit_funding_oi() -> dict | None:
    data = _get_json(f"{_BYBIT}/market/tickers", {"category": "linear", "symbol": "BTCUSDT"})
    lst = (((data or {}).get("result") or {}).get("list") or []) if isinstance(data, dict) else []
    if not lst:
        return None
    r = lst[0]
    return {"funding_rate": _f(r.get("fundingRate")), "oi_btc": _f(r.get("openInterest")),
            "oi_usd": _f(r.get("openInterestValue")), "premium": None}


def bybit_open_interest_24h_ago(now_ms: int, price_usd: float | None) -> dict | None:
    data = _get_json(f"{_BYBIT}/market/open-interest",
                     {"category": "linear", "symbol": "BTCUSDT", "intervalTime": "1h", "limit": 30})
    lst = (((data or {}).get("result") or {}).get("list") or []) if isinstance(data, dict) else []
    if not lst or price_usd is None:
        return None
    target = now_ms - _DAY_MS
    best = None
    for r in lst:
        ts, oi_btc = _f(r.get("timestamp")), _f(r.get("openInterest"))
        if ts is None or oi_btc is None:
            continue
        dist = abs(int(ts) - target)
        if best is None or dist < best[0]:
            best = (dist, int(ts), oi_btc)
    if best is None:
        return None
    _, ts, oi_btc = best
    return {"oi_usd": oi_btc * price_usd, "ts_ms": ts, "age_hours": round((now_ms - ts) / _HOUR_MS, 1)}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def fetch_snapshot(cfg: dict, price_usd: float | None = None) -> dict:
    """Assemble one derivatives snapshot. Missing pieces are None + listed in
    `missing` so the verdict can say exactly what it could not see."""
    dcfg = cfg.get("derivatives", {})
    ct_val = float(dcfg.get("okx_ct_val_btc", 0.01))
    max_pages = int(dcfg.get("liq_max_pages", 20))
    now_ms = _now_ms()
    snap: dict = {
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": None, "funding_rate": None, "premium": None,
        "oi_usd": None, "oi_btc": None, "oi_usd_24h_ago": None, "oi_change_24h_pct": None,
        "oi_ref_age_hours": None,
        "liq_long_usd": None, "liq_short_usd": None, "liq_long_share_pct": None,
        "liq_covered_hours": None, "liq_truncated": None, "liq_source": None,
        "missing": [],
    }

    funding = okx_funding()
    oi_now = okx_open_interest_now()
    oi_ref = okx_open_interest_24h_ago(now_ms)
    if funding or oi_now:
        snap["source"] = "okx"
    else:
        log.warning("OKX funding/OI unavailable — trying Bybit fallback")
        fb = bybit_funding_oi()
        if fb:
            snap["source"] = "bybit"
            funding = {"funding_rate": fb["funding_rate"], "premium": None}
            oi_now = {"oi_btc": fb["oi_btc"], "oi_usd": fb["oi_usd"]}
            oi_ref = bybit_open_interest_24h_ago(now_ms, price_usd)

    if funding:
        snap["funding_rate"] = funding.get("funding_rate")
        snap["premium"] = funding.get("premium")
    if oi_now:
        snap["oi_usd"] = oi_now.get("oi_usd")
        snap["oi_btc"] = oi_now.get("oi_btc")
    if oi_ref:
        snap["oi_usd_24h_ago"] = oi_ref.get("oi_usd")
        snap["oi_ref_age_hours"] = oi_ref.get("age_hours")
    if snap["oi_usd"] and snap["oi_usd_24h_ago"]:
        snap["oi_change_24h_pct"] = round(
            100 * (snap["oi_usd"] - snap["oi_usd_24h_ago"]) / snap["oi_usd_24h_ago"], 2)

    liq = okx_liquidations_24h(now_ms, ct_val, max_pages) if snap["source"] in ("okx", None) else None
    if liq:
        snap["liq_source"] = "okx"
        for k in ("liq_long_usd", "liq_short_usd", "liq_long_share_pct"):
            snap[k] = liq[k]
        snap["liq_covered_hours"] = liq["covered_hours"]
        snap["liq_truncated"] = liq["truncated"]
        snap["liq_counts"] = {"long": liq["liq_long_count"], "short": liq["liq_short_count"],
                              "pages": liq["pages"]}

    for key in ("funding_rate", "oi_change_24h_pct", "liq_long_usd"):
        if snap.get(key) is None:
            snap["missing"].append(key)
    log.info("derivatives: src=%s funding=%s oiΔ24h=%s%% liqL=%s liqS=%s missing=%s",
             snap["source"], snap["funding_rate"], snap["oi_change_24h_pct"],
             snap["liq_long_usd"], snap["liq_short_usd"], snap["missing"])
    return snap


if __name__ == "__main__":
    from .config import load_thresholds, setup_logging
    setup_logging()
    print(json.dumps(fetch_snapshot(load_thresholds()), indent=2, default=str))
