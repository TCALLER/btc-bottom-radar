"""Data acquisition: BTC price OHLC (Kraken primary + fallbacks), Fear & Greed,
and a pluggable on-chain provider. Every fetch has a timeout + retry and
degrades gracefully: on failure it returns None / empty rather than raising, so
one dead source never crashes the daily run."""
from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger("btc-bottom-radar.datasources")

_TIMEOUT = 20
_RETRIES = 3
_HEADERS = {"User-Agent": "btc-bottom-radar/1.0 (monitoring)"}


def _get_json(url: str, params: dict | None = None) -> dict | list | None:
    """GET with retry/backoff. Returns parsed JSON or None on total failure."""
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT, headers=_HEADERS)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - intentional broad catch, degrade gracefully
            log.warning("fetch failed (%s/%s) %s: %s", attempt, _RETRIES, url, exc)
            if attempt < _RETRIES:
                time.sleep(1.5 * attempt)
    return None


# ---------------------------------------------------------------------------
# Price OHLC — returns list[float] of daily/weekly closes oldest -> newest.
# ---------------------------------------------------------------------------

def _kraken_closes(interval: int) -> list[float]:
    """Kraken public OHLC. interval in minutes (1440 daily, 10080 weekly)."""
    data = _get_json(
        "https://api.kraken.com/0/public/OHLC",
        {"pair": "XBTUSD", "interval": interval},
    )
    if not data or data.get("error") or "result" not in data:
        return []
    result = data["result"]
    # first key that is not "last"
    key = next((k for k in result if k != "last"), None)
    if key is None:
        return []
    rows = result[key]  # [time, o, h, l, c, vwap, vol, count]
    return [float(r[4]) for r in rows if r and r[4] is not None]


def _coinbase_closes(granularity: int) -> list[float]:
    """Coinbase Exchange candles. granularity seconds (86400 daily, 604800 weekly).
    Returns up to 300 candles; Coinbase returns newest -> oldest."""
    data = _get_json(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        {"granularity": granularity},
    )
    if not isinstance(data, list) or not data:
        return []
    # each: [time, low, high, open, close, volume]; newest first -> reverse
    rows = sorted(data, key=lambda r: r[0])
    return [float(r[4]) for r in rows]


def _bitstamp_closes(step: int) -> list[float]:
    """Bitstamp OHLC. step seconds (86400 daily, 604800 weekly)."""
    data = _get_json(
        "https://www.bitstamp.net/api/v2/ohlc/btcusd/",
        {"step": step, "limit": 1000},
    )
    if not isinstance(data, dict):
        return []
    ohlc = data.get("data", {}).get("ohlc", [])
    return [float(r["close"]) for r in ohlc if r.get("close") is not None]


def _coingecko_daily_closes() -> list[float]:
    """CoinGecko market chart (last resort, daily only, ~max history)."""
    data = _get_json(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        {"vs_currency": "usd", "days": "max", "interval": "daily"},
    )
    if not isinstance(data, dict):
        return []
    prices = data.get("prices", [])
    return [float(p[1]) for p in prices if p and p[1] is not None]


def fetch_daily_closes(primary: str = "kraken") -> tuple[list[float], str]:
    """Return (closes, source_name). Tries primary then fallbacks in order."""
    chain: list[tuple[str, Callable[[], list[float]]]] = [
        ("kraken", lambda: _kraken_closes(1440)),
        ("coinbase", lambda: _coinbase_closes(86400)),
        ("bitstamp", lambda: _bitstamp_closes(86400)),
        ("coingecko", _coingecko_daily_closes),
    ]
    # move the configured primary to the front
    chain.sort(key=lambda c: 0 if c[0] == primary else 1)
    for name, fn in chain:
        closes = fn()
        if closes and len(closes) >= 50:
            log.info("daily closes from %s (%d points)", name, len(closes))
            return closes, name
    return [], "none"


def fetch_weekly_closes(primary: str = "kraken") -> tuple[list[float], str]:
    """Weekly closes for the 200-week MA. Kraken weekly history is limited, so
    Bitstamp/Coinbase weekly are strong fallbacks; CoinGecko daily is resampled."""
    chain: list[tuple[str, Callable[[], list[float]]]] = [
        ("kraken", lambda: _kraken_closes(10080)),
        ("bitstamp", lambda: _bitstamp_closes(604800)),
        ("coinbase", lambda: _coinbase_closes(604800)),
    ]
    chain.sort(key=lambda c: 0 if c[0] == primary else 1)
    for name, fn in chain:
        closes = fn()
        if closes and len(closes) >= 50:
            log.info("weekly closes from %s (%d points)", name, len(closes))
            return closes, name
    # last resort: resample daily closes to weekly (every 7th close)
    daily, src = fetch_daily_closes(primary)
    if daily:
        weekly = daily[::7]
        if len(weekly) >= 50:
            log.info("weekly closes resampled from daily (%s)", src)
            return weekly, f"{src}-resampled"
    return [], "none"


# ---------------------------------------------------------------------------
# Fear & Greed
# ---------------------------------------------------------------------------

def fetch_fear_greed() -> int | None:
    data = _get_json("https://api.alternative.me/fng/", {"limit": 1, "format": "json"})
    if not isinstance(data, dict):
        return None
    arr = data.get("data") or []
    if not arr:
        return None
    try:
        return int(arr[0]["value"])
    except (KeyError, ValueError, TypeError):
        return None


# On-chain data lives in collector/indicators/onchain.py (OnchainProvider).
