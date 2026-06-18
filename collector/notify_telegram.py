"""Telegram alerting. User-facing strings are Dutch and lead with MEANING +
ACTION (what it means, what to do) rather than an indicator dump — the raw
signal list comes last.

Honest framing: a monitoring tool, not financial advice. It never says 'koop'
or 'verkoop' as an order."""
from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger("btc-bottom-radar.telegram")

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 20

# Dutch labels for the bottom-radar indicators.
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

# Dutch labels for the top-radar indicators.
TOP_LABELS_NL = {
    "pi_cycle_top": "Pi-Cycle Top",
    "mvrv_zscore_high": "MVRV Z-Score (hoog)",
    "mayer_high": "Mayer Multiple (hoog)",
    "rsi_14w_high": "RSI (weekly, hoog)",
    "fear_greed_high": "Fear & Greed (hebzucht)",
    "nupl": "NUPL",
    "puell_multiple": "Puell Multiple",
}

DISCLAIMER = ("ℹ️ Monitoringtool, geen financieel advies. Bodems én tops zijn pas "
              "achteraf te bevestigen. Geen koop-/verkoopopdracht.")
TOP_DISCLAIMER = ("ℹ️ Monitoringtool, geen financieel advies. Tops zijn pas achteraf "
                  "te bevestigen. Geen verkoopopdracht.")

# Plain-language tier phrases.
BOTTOM_TIER_PHRASE = {
    "neutraal": "neutraal — geen bodem in zicht",
    "watch": "waakzaam — eerste bodemtekenen",
    "naderend": "bodem nadert",
    "sterke_bodem_confluentie": "sterke bodem-confluentie — diepe koopzone",
}
TOP_TIER_PHRASE = {
    "neutraal": "neutraal — geen top in zicht",
    "watch": "licht verhoogd",
    "verhit": "verhit — voorzichtig worden",
    "sterke_top_confluentie": "sterke top-confluentie — cyclustop nabij",
}


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
    """Bottom-radar alert events: new_signal_triggered, tier_change, score_delta."""
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


def detect_top_changes(today: dict, last: dict | None) -> list[dict]:
    """Top-radar change events: new top signal fired, or top-tier changed."""
    events: list[dict] = []
    today_sig = set(today.get("top_signals_triggered") or [])
    last_sig = set((last or {}).get("top_signals_triggered") or [])
    new_sig = today_sig - last_sig
    if new_sig:
        events.append({"type": "new_top_signal", "signals": sorted(new_sig)})
    if last is not None and today.get("top_tier") != last.get("top_tier"):
        events.append({"type": "top_tier_change", "from": last.get("top_tier"),
                       "to": today.get("top_tier")})
    return events


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _usd(v) -> str:
    n = _num(v)
    return f"${n:,.0f}".replace(",", ".") if n is not None else "n.b."  # NL thousands


def _eur(v) -> str:
    n = _num(v)
    return f"{round(n):,}".replace(",", ".") if n is not None else "n.b."


def _pct(v) -> str:
    n = _num(v)
    return f"{n:.1f}".replace(".", ",") + "%" if n is not None else "n.b."


def _date(iso: str | None) -> str:
    if not iso or len(iso) < 10:
        return iso or "—"
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"


# ---------------------------------------------------------------------------
# Ladder trap helpers — human-language conditions, price levels, distances
# ---------------------------------------------------------------------------

def _trap_level(trap_id: int, row: dict) -> float | None:
    """Indicative price level at which the trap's condition would be met."""
    if trap_id == 1:
        return _num(row.get("ma_200w"))
    if trap_id == 2:
        s = _num(row.get("sma_200d"))
        return 0.8 * s if s is not None else None
    if trap_id == 3:
        a = _num(row.get("all_time_high_usd"))
        return 0.25 * a if a is not None else None
    return None


def trap_condition_text(trap_id: int, row: dict) -> str:
    lvl = _trap_level(trap_id, row)
    if trap_id == 1:
        return f"koers &lt; 200w-MA ({_usd(lvl)})" if lvl else "koers &lt; 200w-MA"
    if trap_id == 2:
        return f"bodemscore ≥ 60 (~ koers &lt; {_usd(lvl)})" if lvl else "bodemscore ≥ 60"
    if trap_id == 3:
        z = f" ({_usd(lvl)})" if lvl else ""
        return f"capitulatie: Fear&amp;Greed ≤ 10, of −75%{z}, of MVRV-Z ≤ 0,1"
    return ""


def trap_distance_text(trap_id: int, row: dict) -> str:
    price = _num(row.get("price_usd"))
    lvl = _trap_level(trap_id, row)
    if price is None or lvl is None:
        return ""
    d = price - lvl
    if d > 0:
        return f"nog ~{_usd(d)} te zakken"
    return "voorwaarde nu vervuld"


def trap_reason_text(trap_id: int, row: dict) -> str:
    """Why the trap fired, in human language (for the ladder-fire message)."""
    price = _num(row.get("price_usd"))
    if trap_id == 1:
        return f"koers {_usd(price)} ≤ 200w-MA {_usd(row.get('ma_200w'))}"
    if trap_id == 2:
        return f"bodemscore {row.get('bottom_score')} ≥ 60"
    if trap_id == 3:
        parts = []
        fg = row.get("fear_greed")
        dd = _num(row.get("drawdown_from_ath_pct"))
        mvrv = _num(row.get("mvrv_zscore"))
        score = row.get("bottom_score") or 0
        if fg is not None and fg <= 10:
            parts.append(f"Fear&amp;Greed {fg} ≤ 10")
        if dd is not None and dd >= 75:
            parts.append(f"−{_pct(dd)} ≤ −75%")
        if mvrv is not None and mvrv <= 0.1:
            parts.append(f"MVRV-Z {mvrv} ≤ 0,1")
        if score >= 75:
            parts.append(f"bodemscore {score} ≥ 75")
        return " + ".join(parts) if parts else "capitulatievoorwaarde vervuld"
    return ""


def nearest_pending_trap(ladder_state: dict | None) -> int | None:
    """Lowest tranche_id whose status is 'pending'."""
    if not ladder_state:
        return None
    pending = [tid for tid, r in ladder_state.items() if r.get("status") == "pending"]
    return min(pending) if pending else None


def _ladder_label(ladder_state: dict, tid: int) -> str:
    return (ladder_state.get(tid) or {}).get("label", f"Trap {tid}")


# ---------------------------------------------------------------------------
# next_action — plain-language state + what-to-do + nearest pending trap
# ---------------------------------------------------------------------------

def next_action(row: dict, ladder_state: dict | None, cfg: dict) -> dict:
    bottom_tier = row.get("tier", "neutraal")
    top_tier = row.get("top_tier", "neutraal")

    bottom_text = (f"{BOTTOM_TIER_PHRASE.get(bottom_tier, bottom_tier)} "
                   f"({row.get('bottom_score')}/100, "
                   f"{row.get('triggered_count')}/{row.get('available_count')} signalen)")
    top_text = (f"{TOP_TIER_PHRASE.get(top_tier, top_tier)} "
                f"({row.get('top_score')}/100, "
                f"{row.get('top_triggered_count')}/{row.get('top_available_count')} signalen)")

    nid = nearest_pending_trap(ladder_state)
    nearest = None
    if nid is not None:
        nearest = {
            "id": nid,
            "label": _ladder_label(ladder_state, nid),
            "condition": trap_condition_text(nid, row),
            "distance": trap_distance_text(nid, row),
        }

    if top_tier in ("verhit", "sterke_top_confluentie"):
        action = ("📈 Overweeg (deels) <b>winst nemen</b> — de top-radar staat hoog. "
                  "Jouw beslissing (+ Belgische meerwaarde-timing).")
    elif bottom_tier == "sterke_bodem_confluentie":
        action = ("🟢 <b>Diepe koopzone.</b> Overweeg de resterende ladder-trap(pen) "
                  "in te zetten. Jouw beslissing — geen koopopdracht.")
    elif nearest is not None:
        dist = f" ({nearest['distance']})" if nearest["distance"] else ""
        action = (f"⏳ Afwachten. Dichtstbijzijnde koop: <b>{nearest['label']}</b> — "
                  f"{nearest['condition']}{dist}.")
    else:
        action = "✅ Alle ladder-trappen afgehandeld — afwachten."

    return {"bottom_text": bottom_text, "top_text": top_text,
            "action_line": action, "nearest": nearest}


# ---------------------------------------------------------------------------
# Raw signal value formatting (the dump that comes LAST)
# ---------------------------------------------------------------------------

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


def _fmt_top_value(r) -> str:
    v = r.value
    if not r.available:
        return "niet beschikbaar"
    if r.key == "pi_cycle_top":
        return "actief" if v else "niet actief"
    if r.key in ("mayer_high", "nupl", "puell_multiple", "mvrv_zscore_high"):
        return f"{v:.3f} ({r.threshold})"
    if r.key == "rsi_14w_high":
        return f"{v:.1f} ({r.threshold})"
    if r.key == "fear_greed_high":
        return f"{v} ({r.threshold})"
    return f"{v}"


def _signal_lines(results, labels) -> list[str]:
    out = []
    for r in results:
        mark = "✅" if r.triggered else ("➖" if r.available else "⚪")
        out.append(f"{mark} {labels.get(r.key, r.key)}: "
                   f"{html.escape(_fmt_value(r) if labels is LABELS_NL else _fmt_top_value(r))}")
    return out


# ---------------------------------------------------------------------------
# DIGEST — leads with meaning + action; raw signals last
# ---------------------------------------------------------------------------

def format_digest(today: dict, results: list, top_results: list, cfg: dict,
                  ladder_state: dict | None) -> str:
    na = next_action(today, ladder_state, cfg)
    total_bottom = len(cfg["indicators"])
    avail = today.get("available_count") or 0

    dd = today.get("drawdown_from_ath_pct")
    header = (f"📅 <b>{_date(today.get('captured_date'))}</b> · "
              f"BTC <b>{_usd(today.get('price_usd'))}</b>"
              + (f" · −{_pct(dd)} onder ATH" if dd is not None else ""))

    lines = [header, "",
             "📊 <b>Stand van zaken</b>",
             f"Bodem: {na['bottom_text']}",
             f"Top: {na['top_text']}"]
    if avail < total_bottom:
        lines.append(f"⚠️ On-chain tijdelijk onbeschikbaar — score over {avail}/{total_bottom} signalen.")

    lines += ["", "🎯 <b>Wat moet jij nu doen?</b>", na["action_line"]]

    # Jouw ladder (private — budget allowed in Telegram)
    lines += ["", "🪜 <b>Jouw ladder</b>"]
    if ladder_state:
        budget = sum(_num(r.get("amount_eur")) or 0 for r in ladder_state.values())
        lines.append(f"Budget €{_eur(budget)} — jouw beslissing, geen koopopdracht.")
        for tid in sorted(ladder_state):
            tr = ladder_state[tid]
            fired = tr.get("status") == "fired"
            icon = "✅" if fired else "⬜"
            label = tr.get("label", f"Trap {tid}")
            amount = _eur(tr.get("amount_eur"))
            suffix = ""
            if fired and tr.get("fired_on_date"):
                suffix = f" — gevuurd {_date(tr.get('fired_on_date'))}"
            elif na["nearest"] and na["nearest"]["id"] == tid:
                suffix = f" — volgende: {na['nearest']['condition']}"
            lines.append(f"{icon} {label} (€{amount}){suffix}")
    else:
        lines.append("nog niet geïnitialiseerd.")

    # Raw signals dump LAST
    lines += ["", f"📋 <b>Bodemsignalen ({today.get('triggered_count')} van {avail})</b>"]
    lines += _signal_lines(results, LABELS_NL)
    lines += ["", f"📋 <b>Topsignalen ({today.get('top_triggered_count')} van "
              f"{today.get('top_available_count')})</b>"]
    lines += _signal_lines(top_results, TOP_LABELS_NL)

    lines += ["", DISCLAIMER]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CHANGE — short: header + tier/score change + one 🎯 Actie line
# ---------------------------------------------------------------------------

def format_alert(events: list[dict], today: dict, ladder_state: dict | None, cfg: dict) -> str:
    emoji = today.get("tier_emoji", "")
    header = (f"{emoji} <b>BTC Bodemradar — wijziging</b>\n"
              f"{_date(today.get('captured_date'))} · {_usd(today.get('price_usd'))}")
    change = []
    for ev in events:
        if ev["type"] == "tier_change":
            f = (ev.get("from") or "—").replace("_", " ")
            t = (ev.get("to") or "—").replace("_", " ")
            change.append(f"Tier: {f} → <b>{t}</b>")
        elif ev["type"] == "score_delta":
            change.append(f"Score: {ev['from']} → <b>{ev['to']}</b> (Δ{ev['delta']})")
        elif ev["type"] == "new_signal_triggered":
            names = ", ".join(LABELS_NL.get(s, s) for s in ev["signals"])
            change.append(f"Nieuw signaal: <b>{names}</b>")
    na = next_action(today, ladder_state, cfg)
    return "\n".join([header] + change + ["", f"🎯 <b>Actie:</b> {na['action_line']}",
                                          "", DISCLAIMER])


# ---------------------------------------------------------------------------
# TOP ALERT — lead with the action
# ---------------------------------------------------------------------------

def format_top_alert(events: list[dict], top_view: dict) -> str:
    tier_nl = top_view.get("top_tier", "neutraal").replace("_", " ")
    lines = [
        "📈🔔 <b>TOP-RADAR — let op</b>",
        f"Topscore {top_view.get('top_score')}/100 ({tier_nl}). "
        f"Condities kantelen richting een cyclustop.",
        "👉 Overweeg (deels) winst nemen — jouw beslissing + Belgische "
        "meerwaarde-timing. Geen verkoopopdracht.",
    ]
    for ev in events:
        if ev["type"] == "new_top_signal":
            names = ", ".join(TOP_LABELS_NL.get(s, s) for s in ev["signals"])
            lines.append(f"🔔 Nieuw top-signaal: <b>{names}</b>")
    lines += ["", TOP_DISCLAIMER]
    return "\n".join(lines)
