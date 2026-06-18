"""Telegram alerting. User-facing strings are Dutch. Validates the token via
getMe, decides what changed vs the previous snapshot, and sends concise messages.

Framing is honest: a monitoring tool, not financial advice. It never says 'koop'."""
from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger("btc-bottom-radar.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 20

# Dutch labels for indicator keys (dashboard + telegram share this vocabulary).
LABELS_NL = {
    "pi_cycle_bottom": "Pi-Cycle Bodem",
    "ma_200w": "200-weken MA",
    "mayer_multiple": "Mayer Multiple",
    "rsi_14d": "RSI (14d)",
    "drawdown_from_ath_pct": "Daling vanaf ATH",
    "fear_greed": "Fear & Greed",
    "mvrv_zscore": "MVRV Z-Score",
    "sopr": "SOPR",
    "supply_profit_pct": "Supply in winst %",
}

DISCLAIMER = "ℹ️ Monitoringtool, geen financieel advies. Bodems zijn pas achteraf te bevestigen."


def _token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def _chat_id() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


def validate_token() -> bool:
    """Returns True if getMe succeeds. Caller treats False as irreducible secret."""
    try:
        resp = requests.get(_API.format(token=_token(), method="getMe"), timeout=_TIMEOUT)
        ok = resp.ok and resp.json().get("ok") is True
        if ok:
            log.info("telegram getMe ok: @%s", resp.json()["result"].get("username"))
        return ok
    except Exception as exc:  # noqa: BLE001
        log.error("telegram getMe failed: %s", exc)
        return False


def send_message(text: str) -> bool:
    try:
        resp = requests.post(
            _API.format(token=_token(), method="sendMessage"),
            json={"chat_id": _chat_id(), "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=_TIMEOUT,
        )
        ok = resp.ok and resp.json().get("ok") is True
        if not ok:
            log.error("sendMessage failed: %s", resp.text)
        return ok
    except Exception as exc:  # noqa: BLE001
        log.error("sendMessage error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Change detection vs previous snapshot
# ---------------------------------------------------------------------------

def detect_changes(today: dict, last: dict | None, cfg: dict) -> list[dict]:
    """Return a list of alert events: new_signal_triggered, tier_change,
    score_delta_gte_10. Empty list when nothing material changed."""
    events: list[dict] = []
    alert_cfg = cfg["alerting"]
    delta_threshold = alert_cfg.get("score_delta_threshold", 10)

    today_signals = set(today.get("signals_triggered") or [])
    last_signals = set((last or {}).get("signals_triggered") or [])

    new_signals = today_signals - last_signals
    if new_signals and "new_signal_triggered" in alert_cfg["alert_on"]:
        events.append({"type": "new_signal_triggered", "signals": sorted(new_signals)})

    if last is not None and "tier_change" in alert_cfg["alert_on"]:
        if today.get("tier") != last.get("tier"):
            events.append({"type": "tier_change", "from": last.get("tier"),
                           "to": today.get("tier")})

    if last is not None and "score_delta_gte_10" in alert_cfg["alert_on"]:
        delta = abs((today.get("bottom_score") or 0) - (last.get("bottom_score") or 0))
        if delta >= delta_threshold:
            events.append({"type": "score_delta", "delta": delta,
                           "from": last.get("bottom_score"), "to": today.get("bottom_score")})
    return events


# ---------------------------------------------------------------------------
# Message formatting (Dutch)
# ---------------------------------------------------------------------------

def _fmt_price(p) -> str:
    return f"${p:,.0f}" if isinstance(p, (int, float)) else "n.b."


def format_digest(today: dict, results: list, cfg: dict) -> str:
    emoji = today.get("tier_emoji", "")
    tier_nl = today.get("tier", "neutraal").replace("_", " ")
    lines = [
        f"{emoji} <b>BTC Bodem Radar</b> — {today.get('captured_date')}",
        f"Prijs: <b>{_fmt_price(today.get('price_usd'))}</b>",
        f"Bodemscore: <b>{today.get('bottom_score')}/100</b> ({tier_nl})",
        f"Signalen actief: <b>{today.get('triggered_count')} van {today.get('available_count')}</b>",
        "",
    ]
    for r in results:
        mark = "✅" if r.triggered else ("➖" if r.available else "⚪")
        label = LABELS_NL.get(r.key, r.key)
        val = html.escape(_fmt_value(r))  # thresholds contain < > which break HTML mode
        lines.append(f"{mark} {label}: {val}")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _fmt_value(r) -> str:
    v = r.value
    if not r.available:
        return "niet beschikbaar"
    if r.key == "pi_cycle_bottom":
        return "actief" if v else "niet actief"
    if r.key == "drawdown_from_ath_pct":
        return f"{v:.1f}% (drempel {r.threshold})"
    if r.key in ("mayer_multiple", "sopr", "mvrv_zscore"):
        return f"{v:.3f} ({r.threshold})"
    if r.key == "rsi_14d":
        return f"{v:.1f} ({r.threshold})"
    if r.key == "ma_200w":
        return f"${v:,.0f} ({r.threshold})"
    if r.key == "fear_greed":
        return f"{v} ({r.threshold})"
    if r.key == "supply_profit_pct":
        return f"{v:.1f}% ({r.threshold})"
    return f"{v}"


def format_alert(events: list[dict], today: dict) -> str:
    emoji = today.get("tier_emoji", "")
    tier_nl = today.get("tier", "neutraal").replace("_", " ")
    head = [f"{emoji} <b>BTC Bodem Radar — wijziging</b>",
            f"Prijs {_fmt_price(today.get('price_usd'))} · "
            f"score {today.get('bottom_score')}/100 ({tier_nl})", ""]
    body: list[str] = []
    for ev in events:
        if ev["type"] == "new_signal_triggered":
            names = ", ".join(LABELS_NL.get(s, s) for s in ev["signals"])
            body.append(f"🔔 Nieuw signaal actief: <b>{names}</b>")
        elif ev["type"] == "tier_change":
            f = (ev.get("from") or "—").replace("_", " ")
            t = (ev.get("to") or "—").replace("_", " ")
            body.append(f"🔀 Tier gewijzigd: {f} → <b>{t}</b>")
        elif ev["type"] == "score_delta":
            body.append(f"📈 Score verschoven {ev['from']} → <b>{ev['to']}</b> (Δ{ev['delta']})")
    return "\n".join(head + body + ["", DISCLAIMER])
