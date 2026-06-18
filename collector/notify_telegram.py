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

# Plain-language tier phrases (Part 4 wording).
BOTTOM_TIER_PHRASE = {
    "neutraal": "nog rustig, geen bodem in zicht",
    "watch": "we naderen, nog niet in de koopzone",
    "naderend": "dicht bij de koopzone",
    "sterke_bodem_confluentie": "diepe koopzone",
}
TOP_TIER_PHRASE = {
    "neutraal": "geen verkoopsignaal",
    "watch": "licht verhoogd",
    "verhit": "condities kantelen richting een top",
    "sterke_top_confluentie": "condities kantelen richting een top",
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


def trap_amount(ladder_state: dict | None, tid: int) -> float | None:
    return _num((ladder_state or {}).get(tid, {}).get("amount_eur"))


def trap_threshold(tr: dict) -> float | None:
    """Confirmation level: low_since_arm × (1 + confirm_rebound_pct/100)."""
    low = _num(tr.get("low_since_arm_usd"))
    pct = _num(tr.get("confirm_rebound_pct"))
    if low is None or pct is None:
        return None
    return low * (1 + pct / 100.0)


def trap_status_line(tr: dict, row: dict) -> str:
    status = tr.get("status", "pending")
    tid = tr.get("tranche_id")
    if status == "armed":
        return (f"BEWAPEND op {_usd(tr.get('armed_price_usd'))}, "
                f"koop &gt; {_usd(trap_threshold(tr))}")
    if status == "fired":
        return f"KOOP-SIGNAAL {_date(tr.get('fired_on_date'))} ({tr.get('fire_reason') or '—'})"
    return f"wacht op niveau ({trap_condition_text(tid, row)})"


def action_for_trap(tid: int, tr: dict, row: dict, amount: float | None) -> str:
    """Action phrasing for the nearest non-fired trap — ALWAYS shows the € amount."""
    a = _eur(amount)
    confirm_days = int(tr.get("confirm_days") or 2)
    if tr.get("status") == "armed":
        return (f"Trap {tid} (~€{a}) zodra koers &gt; {_usd(trap_threshold(tr))} "
                f"(bevestiging, {confirm_days}d)")
    if tid == 1:
        return f"Trap 1 (~€{a}) zodra koers &lt; {_usd(row.get('ma_200w'))}"
    if tid == 2:
        s = _num(row.get("sma_200d"))
        y = 0.8 * s if s is not None else None
        return f"Trap 2 (~€{a}) zodra bodemscore ≥ 60 (~ koers &lt; {_usd(y)})"
    if tid == 3:
        a3 = _num(row.get("all_time_high_usd"))
        z = 0.25 * a3 if a3 is not None else None
        return (f"Trap 3 (~€{a}) bij capitulatie "
                f"(Fear&amp;Greed ≤ 10, of −75% {_usd(z)}, of MVRV-Z ≤ 0,1)")
    return f"Trap {tid} (~€{a})"


def nearest_nonfired_trap(ladder_state: dict | None) -> int | None:
    """Lowest tranche_id whose status is not 'fired' (pending OR armed)."""
    if not ladder_state:
        return None
    ids = [tid for tid, r in ladder_state.items() if r.get("status") != "fired"]
    return min(ids) if ids else None


def trap_pairs(ladder_state: dict, ids: list[int]) -> tuple[str, float]:
    """Return ('Trap 1 ~€3.036, Trap 2 ~€3.036', total_eur) for the given ids."""
    parts, total = [], 0.0
    for tid in ids:
        amt = trap_amount(ladder_state, tid) or 0.0
        total += amt
        parts.append(f"Trap {tid} ~€{_eur(amt)}")
    return ", ".join(parts) if parts else "geen", total


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


# ---------------------------------------------------------------------------
# next_action — what to do now (always with € amounts)
# ---------------------------------------------------------------------------

def next_action(row: dict, ladder_state: dict | None, cfg: dict) -> str:
    bottom_tier = row.get("tier", "neutraal")
    top_tier = row.get("top_tier", "neutraal")
    today = (row.get("captured_date") or "")[:10]

    fired_today = []
    nonfired = []
    if ladder_state:
        for tid in sorted(ladder_state):
            tr = ladder_state[tid]
            if tr.get("status") == "fired":
                if str(tr.get("fired_on_date") or "")[:10] == today:
                    fired_today.append(tid)
            else:
                nonfired.append(tid)

    if top_tier in ("verhit", "sterke_top_confluentie"):
        return ("📈 Overweeg (deels) <b>winst nemen</b> — jouw beslissing "
                "(+ Belgische meerwaarde-timing).")
    if fired_today:
        labels, _ = trap_pairs(ladder_state, fired_today)
        return f"✅ Vandaag koop-signaal: {labels} — zie de KOOPMOMENT-melding."
    if bottom_tier == "sterke_bodem_confluentie" and nonfired:
        pairs, _ = trap_pairs(ladder_state, nonfired)
        return f"🟢 <b>Diepe koopzone</b> — overweeg de resterende trap(pen): {pairs}."
    nid = nearest_nonfired_trap(ladder_state)
    if nid is not None:
        tr = ladder_state[nid]
        amt = trap_amount(ladder_state, nid)
        return f"⏳ Afwachten. Dichtstbij: {action_for_trap(nid, tr, row, amt)}."
    return "✅ Alle ladder-trappen afgehandeld — afwachten."


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


def _signals_oneline(results, labels) -> str:
    trig = [labels.get(r.key, r.key) for r in results if r.triggered]
    untrig = [labels.get(r.key, r.key) for r in results if r.available and not r.triggered]
    unavail = [labels.get(r.key, r.key) for r in results if not r.available]
    groups = []
    if trig:
        groups.append("✅ " + ", ".join(trig))
    if untrig:
        groups.append("➖ " + ", ".join(untrig))
    if unavail:
        groups.append("⚪ " + ", ".join(unavail))
    return " · ".join(groups) if groups else "—"


DIGEST_DISCLAIMER = ("ℹ️ Geen advies. Bedragen en beslissingen zijn van jou; er wordt "
                     "nooit automatisch gekocht of verkocht.")


# ---------------------------------------------------------------------------
# DIGEST — meaning + action first; ladder with € at every trap; signals last
# ---------------------------------------------------------------------------

def format_digest(today: dict, results: list, top_results: list, cfg: dict,
                  ladder_state: dict | None, positions: dict | None = None) -> str:
    total_bottom = len(cfg["indicators"])
    avail = today.get("available_count") or 0
    emoji = today.get("tier_emoji", "")
    dd = today.get("drawdown_from_ath_pct")
    btier = today.get("tier", "neutraal")
    ttier = today.get("top_tier", "neutraal")

    header = (f"{emoji} <b>BTC Bodem Radar</b> — {_date(today.get('captured_date'))}\n"
              f"Prijs: {_usd(today.get('price_usd'))}"
              + (f" · {_pct(dd)} onder de top" if dd is not None else ""))

    lines = [header, "",
             "📊 <b>Stand van zaken</b>",
             f"• Bodem: {today.get('bottom_score')}/100 — {btier.replace('_', ' ').upper()} "
             f"({BOTTOM_TIER_PHRASE.get(btier, btier)})",
             f"• Top: {today.get('top_score')}/100 — {TOP_TIER_PHRASE.get(ttier, ttier)}"]
    if avail < total_bottom:
        lines.append(f"⚠️ On-chain tijdelijk onbeschikbaar — score over {avail}/{total_bottom} signalen.")

    lines += ["", "🎯 <b>Wat moet jij nu doen?</b>", next_action(today, ladder_state, cfg)]

    # Jouw ladder (private — budget shown, Telegram only)
    lines += ["", "🪜 <b>Jouw ladder (privé)</b>"]
    if ladder_state:
        budget = sum(_num(r.get("amount_eur")) or 0 for r in ladder_state.values())
        for tid in sorted(ladder_state):
            tr = ladder_state[tid]
            amount = _eur(tr.get("amount_eur"))
            lines.append(f"{tid}) ~€{amount} — {trap_status_line(tr, today)}")
        deployed = (positions or {}).get("deployed", 0) or 0
        remaining = budget - deployed
        posline = f"Ingezet: €{_eur(deployed)} · Droog kruit: €{_eur(remaining)}"
        avg = (positions or {}).get("avg_price")
        if avg:
            posline += f" · Gem. instap {_usd(avg)}"
        lines.append(posline)
    else:
        lines.append("nog niet geïnitialiseerd.")

    # Signals LAST (compact one-liners)
    lines += ["", f"🔎 <b>Signalen ({today.get('triggered_count')}/{avail})</b>",
              _signals_oneline(results, LABELS_NL),
              f"Top ({today.get('top_triggered_count')}/{today.get('top_available_count')}): "
              + _signals_oneline(top_results, TOP_LABELS_NL)]

    lines += ["", DIGEST_DISCLAIMER]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CHANGE — short: header + tier/score change + one 🎯 Actie line
# ---------------------------------------------------------------------------

def format_alert(events: list[dict], today: dict, ladder_state: dict | None, cfg: dict) -> str:
    emoji = today.get("tier_emoji", "")
    tier_nl = today.get("tier", "neutraal").replace("_", " ")
    header = (f"{emoji} <b>BTC Bodem Radar — wijziging</b>\n"
              f"Prijs {_usd(today.get('price_usd'))} · "
              f"score {today.get('bottom_score')}/100 ({tier_nl})")
    change = []
    for ev in events:
        if ev["type"] == "tier_change":
            f = (ev.get("from") or "—").replace("_", " ")
            t = (ev.get("to") or "—").replace("_", " ")
            change.append(f"🔀 Tier: {f} → <b>{t}</b>")
        elif ev["type"] == "score_delta":
            change.append(f"📈 Score: {ev['from']} → <b>{ev['to']}</b> (Δ{ev['delta']})")
        elif ev["type"] == "new_signal_triggered":
            names = ", ".join(LABELS_NL.get(s, s) for s in ev["signals"])
            change.append(f"🔔 Nieuw signaal: <b>{names}</b>")
        elif ev["type"] == "ladder_event":
            change.append(f"🪜 {ev['text']}")
    action = next_action(today, ladder_state, cfg)
    return "\n".join([header] + change + ["", f"🎯 {action}", "",
                                          "ℹ️ Geen advies."])


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
