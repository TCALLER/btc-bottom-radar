# BTC Bottom Radar

A daily Bitcoin **cycle-bottom monitor**. It pulls historically reliable bottom indicators, scores how
many sit in their "bottom zone" (0–100 + tier), stores each day's snapshot in Supabase, alerts on
Telegram when the picture changes, and serves a small read-only React dashboard.

> ⚠️ **Monitoring tool, not financial advice.** It never places orders and never says "buy".
> Cycle bottoms can only be confirmed in hindsight — this measures *tilt toward a bottom*, nothing more.

**Live dashboard:** https://tcaller.github.io/btc-bottom-radar/ — data-driven, refreshes on page load.

## Architecture

```
Kraken / alternative.me / (optional on-chain)
        │  fetch (timeout + retry, degrade gracefully)
        ▼
collector/  ──► scoring ──► Supabase btc.indicators (service_role, upsert/day)
        │                         │
        ├──► Telegram (Dutch) ◄───┘  alert on change + daily digest, logged to btc.alerts
        ▼
dashboard/  ──► reads btc.latest + btc.indicators via anon key (RLS: select only)
```

- **Database**: schema `btc` in a **dedicated** Supabase project `btc-bottom-radar`
  (ref `ajunjsegdeyqjtjllnxg`). The dashboard's public anon key is isolated to this BTC-only project.
- **Schedule**: GitHub Actions, daily `30 5 * * *` UTC (~07:30 Europe/Brussels).

## Indicators (config/thresholds.json)

Recalibrated for the **diminishing-extremity regime** (bottoms get less extreme each cycle): lean on
the self-normalizing MVRV-Z, down-weight fuzzy signals, set fixed-extremity thresholds to the modern
bottom. Weights (relative): MVRV-Z 22, 200-week MA 18, supply-in-profit 15, Mayer 12, Pi-Cycle Bottom
12, Fear&Greed 10, drawdown 8, SOPR 8, RSI(14) 6. Bottom thresholds: Pi-Cycle Bottom (EMA150<SMA471),
200-week MA (price≤MA), **Mayer < 0.70**, RSI(14) < 30, **drawdown ≥ 65%**, Fear&Greed ≤10, MVRV-Z
≤0.1, SOPR <1.0, supply-in-profit ≤55. Top radar thresholds were lowered (peaks compress each cycle).

> ⚠️ All fixed-extremity threshold values are **calibration from approximate historical readings —
> analytical, not proven**, and (for on-chain/sentiment) not backtestable for lack of a free
> historical series. See `DECISIONS.md` and the config `_caveat` notes.

Score = `round(100 * Σ weight(available & triggered) / Σ weight(available))`. Tiers: `neutraal` ⚪,
`watch` 🟡, `naderend` 🟠, `sterke_bodem_confluentie` 🔴.

## Local usage

```bash
python -m venv venv && ./venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source venv/bin/activate                                                         # *nix
python -m pytest -q                 # unit tests for the TA math
python -m collector.main            # collect + persist + alert-on-change
python -m collector.main --digest   # also send a Telegram status digest
```

Environment lives in `.env` (see `.env.example`). On-chain is **enabled** via the free
bitcoin-data.com source (`ONCHAIN_PROVIDER=bitcoin_data`) — MVRV Z-Score, SOPR and supply-in-profit %
(the last derived as `supplyProfitBtc / circulating_supply × 100`). Set `none` to disable, or
`glassnode` + `GLASSNODE_API_KEY` for the paid provider. The free source allows ~10 requests/hour;
the daily run uses ~4 and degrades gracefully (on-chain marked unavailable) if rate-limited.

## Dashboard

```bash
cd dashboard
npm install
npm run build        # outputs dist/
npm run preview      # local preview server
```

Reads `dashboard/.env.local` (`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_DB_SCHEMA=btc`).
The dashboard shows **two gauges** — "Bodem" (bottom) and "Top" — plus per-radar indicator tables and
a dual score-over-time chart. The buy ladder and budget are **never** shown (privacy).

## Top / sell radar

A symmetric counterpart to the bottom radar (`collector/top_radar.py`, `top_indicators` in config):
Pi-Cycle Top (SMA111 ≥ 2×SMA350), MVRV Z-Score ≥ 7, Mayer Multiple > 2.4, weekly RSI > 80,
Fear & Greed ≥ 90, NUPL > 0.75, Puell Multiple > 4. `top_score = round(100·Σweight(available &
triggered)/Σweight(available))`; tiers `neutraal`/`watch`/`verhit`/`sterke_top_confluentie`. It
**alerts only** (tilt toward a top, honest framing, Belgian meerwaarde-timing in mind) — it never
builds a fixed-euro sell ladder and never says "verkoop".

## Buy ladder — validation ladder (notify-only — never trades)

A signal-driven buy ladder lives **in Telegram + CLI only** — budget/plan are personal, stored in
`btc.ladder_state`/`btc.positions` with **no anon policy** (never on the public dashboard). Config in
`config/ladder.json` (budget €10.121, 3 tranches 30/30/40%, `confirm_days`, per-trap
`confirm_rebound_pct`). Rules are explicit predicates (no `eval`).

**Three-phase state machine** (`pending → armed → fired`): a tranche **arms** when its value rule is
true, then only **fires** once price rebounds `confirm_rebound_pct%` above its post-arm low for
`confirm_days` in a row — a fresh low resets the streak (anti-bull-trap), so it confirms a turn
before signalling a buy. An **uptrend fallback** fires the remaining tranches once price closes above
the 200-day MA (the deep levels likely won't return that cycle). Every message shows the **€ amount
at every trap**, every time. It never issues a buy order.

```bash
python -m collector.ladder --status                              # 3-phase state, € per trap, ingezet/droog kruit
python -m collector.ladder --preview arm --trap 1 --send-test    # preview ARMEER message (🧪, no DB write)
python -m collector.ladder --preview fire --trap 2 --send-test   # preview KOOPMOMENT message
python -m collector.ladder --preview uptrend --send-test         # preview VANGNET message
python -m collector.ladder --backtest                            # full-history price-only backtest (no DB write)
python -m collector.ladder --mark-bought 1 3036 --price 62000 --note "..."   # record a buy (private)
python -m collector.ladder --positions                           # list buys + totals
```

`--preview` and `--backtest` are **side-effect-free** (no writes to `btc.ladder_state`/`positions`).
`--mark-bought` records a realised buy in `btc.positions` and marks a real trap fired
(`fire_reason='manual'`).

## Telegram messages (action-led)

Messages lead with **meaning + action**, not an indicator dump. `next_action()` derives the
plain-language bottom/top state, a "what to do" line, and the nearest *pending* ladder trap (with
its human condition, price level and distance). The **digest** has fixed sections — header
(date · price · % under ATH), "Stand van zaken", "Wat moet jij nu doen?", "Jouw ladder" (private —
budget shown only in Telegram), and the ✅/➖/⚪ signal lists last — plus a ⚠️ line when on-chain is
temporarily unavailable. The **change** message is a short header + the tier/score delta + one
"🎯 Actie" line. A fired ladder trap sends a "🪜🔔 KOOPMOMENT" message; an armed trap a "🟡 BEWAPEND"
(ARMEER) message; the uptrend fallback a "🚀 VANGNET" message; the top radar "📈🔔 TOP-RADAR — let op".
None of them is ever a buy/sell order.

**Reduced-noise scheduling:** the GitHub Actions cron stays daily, but the code decides what to send.
A CHANGE alert goes out only on a meaningful event (tier change, |score Δ| ≥ `score_change_alert_threshold`,
or a trap armed/fired/uptrend). The FULL digest is sent only on `--digest` or the weekly
`digest_weekday` (Monday). A quiet, event-free non-digest day writes the row and sends nothing.
ARMEER/KOOPMOMENT/VANGNET always send when they occur.

## Interactive Telegram bot (read-only)

A Supabase Edge Function (`supabase/functions/telegram-bot/index.ts`) lets the owner query state by
typing commands. It is **read-only** — it reads the `btc` tables and replies; it never trades.

Commands (Dutch, € shown at every trap): `/btc` (LIVE Kraken price + 24h %, vs the daily snapshot
and vs the ladder levels — read-only, no DB write), `/radar` (price, bottom & top score, nearest
non-fired trap), `/ladder` (private — per-trap status + €, ingezet/droog kruit/gem. instap),
`/positions` (recorded buys + totals), `/digest` (the last stored digest), `/help`.

**Locked down:** the webhook verifies the `x-telegram-bot-api-secret-token` header against a secret
(else 401) and only acts on the owner's `chat.id` (any other chat is silently ignored with 200).
Secrets live in the Edge Function env (never in the repo); deployed `verify_jwt=false` because the
secret header + chat allowlist are the gate. Endpoint:
`https://ajunjsegdeyqjtjllnxg.supabase.co/functions/v1/telegram-bot`.

The radar runs on its **own dedicated bot** (`@tom_btcradar_bot`) — used by both the collector and the
Edge Function webhook — so it never conflicts with the separate bot that backs the local Claude
Telegram channel (a Telegram bot can be webhooked **or** long-polled, not both). See `DECISIONS.md`.

## Database schema

`db/schema.sql` is the canonical, idempotent source-of-truth for the `btc` schema (tables, RLS,
grants, and the `btc.latest` view). The view is declared `WITH (security_invoker = true)` so it
runs with the caller's RLS rather than the owner's (no SECURITY DEFINER bypass). Because the view
uses `select *`, **always recreate it from this file after adding any column** to `btc.indicators`.

## Notes & stubs

- The Telegram bot token currently configured resolves to **@flowgenius_bot** (it reaches the target
  chat). To use a different bot, set `TELEGRAM_BOT_TOKEN` in `.env` and the repo secret.
- SOPR's "sustained ~14d" rule is approximated on the spot value (no intraday history is persisted).
- BTC now runs on its **own** Supabase project (`ajunjsegdeyqjtjllnxg`); the dashboard/bundle carry
  no shared-project key. Originally prototyped inside a shared project, since migrated out and that
  project restored to baseline — see `DECISIONS.md` (migration section + before/after diffs).
