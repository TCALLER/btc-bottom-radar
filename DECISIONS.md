# DECISIONS — BTC Bottom Radar

Tactical calls made during the autonomous build. Date: 2026-06-18.

## Phase 0 — Preflight capability detection

| Capability | Result | Path chosen |
|---|---|---|
| Python | 3.11.9 at `python` | venv at `./venv` |
| Node / npm | v24.14.0 / 11.9.0 | dashboard build |
| git / gh | git 2.53; gh authed as **TCALLER** | scheduler = **GitHub Actions** |
| Supabase MCP | `list_projects` works | All Supabase work via **MCP** |
| Supabase access token | found in `~/.claude/history.jsonl` (`sbp_…`) | used for Management API (api-keys reveal, PostgREST config) |
| 1Password `op` | installed but **not signed in**, no `OP_SERVICE_ACCOUNT_TOKEN` | skipped silently per spec |
| Vercel CLI | 50.37.0 present | deploy target candidate (token check in Phase G) |

## Phase 0b — Secrets discovery (zero human questions needed)

- **Supabase URL / anon / service_role**: fetched for hattrick (`<hattrick-ref redacted>`) via MCP
  (`get_project_url`, `get_publishable_keys`) + Management API `GET /v1/projects/{ref}/api-keys?reveal=true`
  using the discovered access token. service_role obtained this way (MCP does not expose it directly).
- **Telegram bot token**: discovered at `~/.claude/channels/telegram/.env`.
  - ⚠️ **Discrepancy logged**: that token belongs to **@flowgenius_bot** (id 8689553511), not @tomvault_bot
    as named in the brief. `getMe` succeeds and `getChat` confirms it **can reach the target chat**
    (the configured private chat id). Since deliverability is what matters and the
    token is the only one discoverable, it is used. If the owner specifically wants @tomvault_bot, drop
    that bot's token into `.env` (`TELEGRAM_BOT_TOKEN=`) — nothing else changes.
- **Deploy token**: see Phase G.

Result: both potentially-irreducible secrets (Telegram token, Supabase access) were **found** →
the entire build ran with **zero human questions**.

## Phase A — Database (reuse hattrick, isolate in `btc` schema)

- **No new project created.** All objects in schema `btc` of project `hattrick` (ref `<hattrick-ref redacted>`,
  eu-central-1, ACTIVE_HEALTHY).
- Schema, tables (`btc.indicators`, `btc.alerts`), indexes, RLS, anon SELECT policy on `indicators`,
  `btc.latest` view, and anon grants applied via MCP `execute_sql` (standalone — not added to hattrick's
  migration folder).
- **Extra grant added** after first run hit `permission denied for schema btc`: granted USAGE +
  ALL on tables/sequences in `btc` to **service_role** (collector writes). Default privileges set too.
  All strictly inside `btc`.
- **Exposed-schema setting** (the only shared setting touched): read current PostgREST `db_schema`
  via Management API (`public,graphql_public`), **appended** `btc` →
  `public,graphql_public,btc` via `PATCH /v1/projects/{ref}/postgrest`. Additive only.

### HARD GUARDRAIL — hattrick `public` untouched (before/after diff)

Baseline (BEFORE) and AFTER snapshots of `public` are **identical**:

- **Tables (5)**: `match_predictions`, `players`, `snapshots`, `youth_players`, `youth_skill_log`
- **Views**: none
- **Policies (10)**: anon-read + service_role-all on each of the 5 tables

Diff result: **zero changes** to `public` tables/views/policies. The only additions cluster-wide are
the `btc` schema + its 2 tables/1 view/1 policy/grants, and the additive exposed-schema entry. ✅

## Phase B/C — Config & collector

- Thresholds in `config/thresholds.json`; no magic numbers in code.
- Price: **Kraken** primary (XBTUSD OHLC daily 1440 / weekly 10080), fallbacks Coinbase → Bitstamp →
  CoinGecko (daily) and weekly resample as last resort. First run pulled 721 daily + 664 weekly closes.
- Fear & Greed: alternative.me.
- On-chain: `ONCHAIN_PROVIDER=none` by default (MVRV/SOPR/supply-in-profit marked `available=false`,
  weights excluded from the denominator). Pluggable `bitcoin-data` and `glassnode` providers implemented.
- Score = `round(100 * earned / possible)` over available weights; tiers per config.
- Pure TA math (`collector/ta.py`) unit-tested against `tests/fixtures/prices.json` — 6 tests pass.

## Phase D/E — Alerting & verification

- Telegram messages in Dutch, HTML mode (dynamic values HTML-escaped — `<`/`>` in thresholds had
  broken the first digest send; fixed).
- Change detection vs the latest stored snapshot (fetched before upsert): `new_signal_triggered`,
  `tier_change`, `score_delta >= 10`. Same-day re-runs compare against today's own row → no duplicate alerts.
- **Verified end-to-end**: `python -m collector.main --digest` → row id=1 for 2026-06-18
  (score 25, tier `watch`, 1/6 signals). A change alert and a digest were **delivered** to chat
  1277494397 (`delivered=true` in `btc.alerts`).

## Phase F — Schedule

- **GitHub Actions** (gh authed). Workflow `.github/workflows/daily.yml`, cron `30 5 * * *` UTC
  (~07:30 Brussels) + `workflow_dispatch`. Repo secrets set via `gh secret set`. (see final summary)

## Phase G — Deploy

- Vercel CLI present but **token invalid** (`vercel whoami` rejected); no `VERCEL_TOKEN` and no
  Cloudflare token discoverable. Per spec, **no interactive login** → fallback to **local preview**.
- `npm run build` is green (874 modules). `npm run preview` serves the production build at
  **http://localhost:4173/** (also on LAN, e.g. http://192.168.2.11:4173/). Verified HTTP 200 +
  title "BTC Bodem Radar"; the anon-key read of `btc.latest` returns today's row
  (price ~$63.8k, score 25, tier `watch`, 1/6 signals) — i.e. the dashboard renders live data.
- To get a hosted URL later: `vercel login` then `cd dashboard && vercel --prod` (env baked from
  `dashboard/.env.local`), or enable GitHub Pages on the repo.

## Phase F (proof)

- `workflow_dispatch` run **27765249546** is fully green (checkout → setup-python → install →
  pytest → `collector.main --digest`). The daily `schedule` cron is therefore active.
- First dispatch run failed once on `Invalid URL` (a stray-whitespace secret); fixed by `.strip()`
  on the Supabase URL/key in `get_client` + re-setting the secret cleanly. Re-run green.

---

# Follow-up tasks (2026-06-18)

## TASK B — Enable the 3 on-chain signals (full 9-indicator score)

- **Source: bitcoin-data.com (bgeometrics), free tier.** Endpoints probed live and parsers adapted
  to the real JSON (single-object `/last` responses, camelCase fields):
  - `/v1/mvrv-zscore/last` → `mvrvZscore` (observed **0.4234**, ref ≈0.4 ✓)
  - `/v1/sopr/last` → `sopr` (observed **0.9948**, ref 0.97–0.99 ✓)
  - `/v1/supply-profit/last` → `supplyProfitBtc` — this is an **absolute BTC amount**
    (~1.062e7), **not** a percentage. The free tier has no percent endpoint (all `*-percent`
    slugs 404). "Percent supply in profit" is by definition `profit_btc / circulating_supply × 100`,
    so it is **computed** from that real reading plus a free circulating-supply source
    (`blockchain.info/q/totalbc`, CoinGecko fallback) ≈ 20.04M BTC → **≈53.0%**. No value invented.
- **Free-tier limit is ~10 requests/hour per IP.** The daily run makes only ~4 calls. Probing during
  development exhausted the hour's budget; the live verification run was deferred until reset.
  On GitHub Actions (shared runner IPs) a 429 is possible — handled gracefully: on 429/failure each
  on-chain value is `None` → `available=false` → excluded from the score (never crashes).
- Implemented in **`collector/indicators/onchain.py`** (`OnchainProvider`, accepts `bitcoin_data`
  or `bitcoin-data`; old copy removed from `datasources.py`). Sanity bands warn-but-store if a value
  is out of range. `ONCHAIN_PROVIDER=bitcoin_data` set in `.env` and as a repo secret.
- If the free source ever stops serving these series, set `ONCHAIN_PROVIDER=glassnode` +
  `GLASSNODE_API_KEY` (paid) — not enabled autonomously.

## TASK A — Permanent free always-on dashboard URL

- **Cloudflare Pages (preferred): not usable non-interactively.** `wrangler` absent; no
  `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID` in env; `~/.cloudflared` holds only a *tunnel* cert
  for `*.flowgenius.xyz` (localhost services), not a Pages API token. Deploy would require
  `wrangler login` (interactive) → skipped per the no-interactive rule.
- **Vercel: token still invalid.** Skipped.
- **Chosen: GitHub Pages via Actions.** Free plan does **not** allow Pages on a *private* repo
  (confirmed: HTTP 422 "Your current plan does not support GitHub Pages for this repository").
  Per the brief's sanctioned option, **the repo was made public.**
  - **Safety verified before publishing**: scanned the *entire* git history — **no** JWT/`sbp_`/
    `sb_secret_`/Telegram token in any blob; `.env`, `.sbkeys.json`, `dashboard/.env.local` were
    never committed; `dist/` is gitignored. The built bundle contains only the Supabase URL + anon
    key (public-by-design, read-only RLS; `btc.alerts` has no anon policy). Git **history was
    rewritten to a single clean commit** to also drop one line of PII (a real name/username from a
    `getChat` response) that had been in an earlier `DECISIONS.md` revision.
  - Pages workflow `.github/workflows/pages.yml` builds `dashboard/` (anon URL/key from repo
    secrets, `VITE_BASE=/btc-bottom-radar/`) and publishes `dist` via `actions/deploy-pages`
    (uses `GITHUB_TOKEN`, no extra secret).
- **Live dashboard URL: https://tcaller.github.io/btc-bottom-radar/** (data-driven — daily rows
  appear on page load, no redeploy needed). Local `npm run preview` remains as a fallback.

---

# Migration to a dedicated BTC project (2026-06-18)

**Why:** the public Pages bundle had shipped the *shared* hattrick anon key, and hattrick's own
tables are anon-readable. Fix: give BTC its **own** Supabase project (isolated anon key), then remove
the `btc` schema from hattrick and restore hattrick to baseline. Owner declined rotating hattrick's
anon key (game data; brief exposure accepted).

- **Phase 0 — stop exposure:** repo made **private** first, so the public bundle carrying hattrick's
  key went offline immediately.
- **Phase 1 — new project:** `get_cost` = **$0/month** (Free plan, 2nd active project). Created
  **`btc-bottom-radar`** ref **`ajunjsegdeyqjtjllnxg`** (eu-central-1) via the Management API
  (chosen over MCP so a strong DB password could be set and recorded — stored in `.env` as
  `SUPABASE_DB_PASSWORD`; the app itself uses the REST service key, not direct Postgres). Reached
  ACTIVE_HEALTHY; anon + service_role keys captured.
- **Phase 2 — schema:** identical `btc` schema/tables/RLS/view + anon **and** service_role grants
  applied via MCP; `btc` appended to PostgREST `db_schema` (`public,graphql_public,btc`) on the new
  project. No code changes (same schema/table names).
- **Phase 2b — history:** hattrick `btc.indicators` held only today's row, which Phase 4 regenerates
  (upsert on `captured_date`); copy would be overwritten → skipped (sanctioned).
- **Phase 3 — repoint:** `.env`, `dashboard/.env.local`, and the **GitHub repo secrets**
  (`SUPABASE_URL/SERVICE_KEY/ANON_KEY`, via stdin) all point at the new project. `SUPABASE_DB_SCHEMA`
  unchanged (`btc`).
- **Phase 4 — verify new project:** `collector.main --digest` wrote row id=1 (score **50**, tier
  `naderend`, **9/9 available**, MVRV 0.4234 / SOPR 0.9948 / supply-in-profit 52.99%); digest +
  change alert delivered; anon read of `btc.latest` on the new project returns the row.
- **Phase 5 — republish safely:** rebuilt with `VITE_BASE=/btc-bottom-radar/`; **DEPLOYED bundle
  check: old ref `<hattrick-ref redacted>` count = 0 (ABSENT), new ref `ajunjsegdeyqjtjllnxg` present.**
  Repo set **public** again (now only an isolated key for a BTC-only project), Pages re-enabled +
  redeployed (run success), live URL HTTP 200 + title "BTC Bodem Radar".
- **Phase 6 — restore hattrick (ref `<hattrick-ref redacted>`):**
  - `drop schema if exists btc cascade;` → `btc` gone.
  - exposed schemas reverted `public,graphql_public,btc` → **`public,graphql_public`** (the original
    value captured at first build).
  - **before/after diff:** `public` = **5 tables, 0 views, 10 policies** (unchanged:
    `match_predictions, players, snapshots, youth_players, youth_skill_log`); `btc_schema_exists = 0`.
    Anon now gets `PGRST106 Invalid schema: btc` — exposure closed. **hattrick is back to baseline.**

**Result:** the public repo + deployed bundle contain **no hattrick ref or key** (verified count 0);
BTC runs entirely on its own project `ajunjsegdeyqjtjllnxg`; hattrick is untouched/baseline.

---

# Buy ladder + top/sell radar + dashboard top gauge (2026-06-18)

## PART A — Buy ladder (notify-only, never trades)
- `config/ladder.json`: budget **€10.121**, 3 tranches 30/30/40% →
  **€3.036 / €3.036 / €4.048** (10121×0.40 = 4048.4 → €4.048; the brief's ~€4.049 was approximate).
- Rules are explicit predicates in `collector/ladder.py` (no `eval`):
  `tier_naderend_and_ma200w`, `score_gte_60`, `score_gte_75_or_capitulation`.
- **Privacy:** `btc.ladder_state` has RLS on and **no anon policy / no anon grant** — the budget
  and plan are never readable via the public dashboard key. Ladder lives in Telegram + CLI only.
- Integrated into the daily flow: after the row is persisted, `ladder.evaluate(row)` fires each
  pending tranche whose rule is true exactly once (Dutch Telegram + DB update + `alerts` log).
  Idempotent; wrapped so a ladder error can never break the daily collection.
- **Simulation (`--simulate`) is side-effect-free** — builds a synthetic row from the latest real
  row + `--set k=v` / `--score` overrides, recomputes tiers from config, prints a table, optionally
  sends a labelled `🧪 SIMULATIE` test. It never writes to `btc.ladder_state`.
- Fixed a Windows-console crash: CLI now forces UTF-8 stdout (cp1252 choked on €/emoji/↳).

### Proof — `--status`
```
Buy-ladder — BTC budget €10.121 (EUR)
1  Trap 1 — naderend + 200w-MA aangeraakt   3.036  pending
2  Trap 2 — score >= 60                      3.036  pending
3  Trap 3 — sterke confluentie of capitulatie 4.048  pending
```
### Proof — `--simulate --score 76 --send-test`
```
1 Trap 1  rule_true=False  WOULD_FIRE=nee   (tier=sterke_bodem_confluentie, ma_200w=False)
2 Trap 2  rule_true=True   WOULD_FIRE=JA    (bottom_score=76 >=60)
3 Trap 3  rule_true=True   WOULD_FIRE=JA    (score=76 >=75)
🧪 SIMULATIE Telegram verzonden: True
```
A second `--status` was **unchanged** (all pending) → simulation proven side-effect-free.

### Trap 1 fire status — honest note
Trap 1's condition (`tier=='naderend'` AND `ma_200w` triggered) was true earlier today
(score 50 / naderend at 16:32 UTC), but the ladder code's first live run was 18:12 UTC, by which
time **price ($62,611) had risen just above the 200-week MA ($62,596)** → `ma_200w` no longer
triggers, score fell to **38 / watch**. So Trap 1 **correctly did NOT fire** (rule false). No fake
fire was created. `btc.alerts` for the 18:12 run shows only `change` + `digest` (real, delivered);
`ladder_state` remains all `pending`. If price dips back below the 200w-MA, Trap 1 fires that day.

## PART B — Top / sell radar (symmetric, alerts only)
- `top_indicators` + `top_score_tiers` in `config/thresholds.json`; `collector/top_radar.py`
  computes 7 signals, scored with the same weighted-normalized math (`scoring.compute_top_score`).
- Indicators (today's live values): Pi-Cycle Top (SMA111 vs 2×SMA350) = false; MVRV high (≥7) =
  0.4234; Mayer high (>2.4); weekly RSI (>80) = **35.27**; F&G greed (≥90) = 15; **NUPL (>0.75) =
  0.178** (free `/v1/nupl/last` → `nupl`); **Puell (>4) = 0.647** (`/v1/puell-multiple/last` →
  `puellMultiple`). All probed live; optional on-chain ones degrade gracefully.
- New additive columns on `btc.indicators` (anon-readable — non-personal): `rsi_14w`,
  `pi_cycle_top`, `nupl`, `puell_multiple`, `top_score`, `top_tier`, `top_signals_triggered`.
- Top alert (Telegram, on top-tier change / new top signal) — honest tilt-not-sell framing, mentions
  Belgian meerwaarde-timing; never a fixed-euro sell ladder. **Today top_score = 0 (neutraal, 0/7)**.
- Top-sim proof: `--simulate --set mvrv_zscore=8 --set top_score=80 --send-test` →
  `top_score=80 (sterke_top_confluentie)`, `🧪 SIMULATIE Telegram verzonden: True`.

## PART C — Dashboard second gauge
- Second gauge "Top" beside "Bodem"; separate Bodem/Top indicator tables; dual score-over-time
  chart (Bodemscore + Topscore). Dutch labels, dark theme. **Ladder/budget NOT on the dashboard.**
- Rebuilt + redeployed via the Pages workflow; **deployed bundle re-verified**: old hattrick ref
  count = 0, new ref `ajunjsegdeyqjtjllnxg` present. Live: https://tcaller.github.io/btc-bottom-radar/

## Today's reading (2026-06-18, project ajunjsegdeyqjtjllnxg)
**bottom_score = 38 (watch, 3/9: pi_cycle_bottom, sopr, supply_profit_pct)** ·
**top_score = 0 (neutraal, 0/7)** · price ~$62.6k.

## Post-verify fixes
- **`btc.latest` view rebuilt.** It had been created with `select *` before the top columns were
  added, so PostgREST returned `column latest.top_score does not exist` for the dashboard's gauge
  query. Recreated `create or replace view btc.latest as select *` (re-expands `*`) + re-granted
  anon select; anon read of `btc.latest` now returns `top_score/top_tier/rsi_14w/nupl/puell_multiple`.
- **Privacy re-verified:** anon `GET /rest/v1/ladder_state` → `42501 permission denied` (no anon
  policy/grant). The ladder/budget is not reachable with the public key.

---

# Action-led Telegram messages (2026-06-18)

Rewrote `collector/notify_telegram.py` so messages **lead with meaning + action**, not an
indicator dump. New helper `next_action(row, ladder_state, cfg)` returns plain-language bottom/top
state, a "what to do" line, and the nearest *pending* ladder trap (lowest `tranche_id` with
status `pending`) with its human condition + price level + distance.

**What-to-do logic:** top_tier ∈ {verhit, sterke_top_confluentie} → take-profit cue; elif
bottom tier == sterke_bodem_confluentie → "diepe koopzone, overweeg resterende trap(pen)"; elif a
trap fired this run → handled by `ladder.py`'s own fire message; else → "afwachten; dichtstbijzijnde
koop = <nearest pending trap>". Trap conditions in human terms: Trap 1 `koers < 200w-MA ($X=ma_200w)`
(afstand price−X); Trap 2 `bodemscore ≥ 60 (~ koers < $Y=0.8·sma_200d)`; Trap 3 `capitulatie:
F&G ≤10, of −75% ($Z=0.25·ath), of MVRV-Z ≤0,1`.

- **DIGEST** sections (in order): header (DD/MM/YYYY + prijs + % onder ATH) · "Stand van zaken"
  (Bodem + Top in mensentaal) · "Wat moet jij nu doen?" · "Jouw ladder" (reads `btc.ladder_state`
  via service-role; budget shown — Telegram is private) · "Bodemsignalen (X van Y)" + "Topsignalen
  (X van Y)" ✅/➖/⚪ lists last · disclaimer. A ⚠️ line appears when `available_count < 9`
  ("On-chain tijdelijk onbeschikbaar — score over X/Y signalen").
- **CHANGE**: short — header + tier/score change + one "🎯 Actie" line (nearest trap).
- **LADDER-FIRE** (`ladder.py`): "🪜🔔 KOOPMOMENT — {label} bereikt / Voorwaarde vervuld: {reden} /
  👉 Overweeg ~€{bedrag} in te zetten. Jouw beslissing — geen koopopdracht. / Daarna nog open:
  {resterende traps}."
- **TOP-ALERT**: "📈🔔 TOP-RADAR — let op / Topscore {score}/100 ({tier}) … 👉 Overweeg (deels)
  winst nemen — jouw beslissing + Belgische meerwaarde-timing. Geen verkoopopdracht."
- The ladder now runs **before** the digest in `main.py`, so "Jouw ladder" reflects same-run fires.

### Proof (delivered=true)
- `--simulate --score 76 --send-test` → CLI shows Trap 2 + 3 WOULD fire; a 🧪 SIMULATIE ping in the
  new action-led format arrived.
- `python -m collector.main --digest` → real digest delivered. Today: **bottom_score 38 (watch,
  3/9)**, **top_score 0 (neutraal, 0/7)**, BTC ~$62.6k, −50,3% onder ATH; nearest buy = Trap 1
  (koers < 200w-MA $62.596, nog ~$7 te zakken).

---

# Validation ladder + backtest + positions + reduced-noise (2026-06-18)

## Part 1/2 — config + schema
- `config/ladder.json`: `confirm_days:2`, `uptrend_rule:price_above_sma200d`, tranches with
  `value_rule` + `confirm_rebound_pct` (3/5/8%). `notify` block in `thresholds.json`
  (`digest_weekday:0` = Monday, `score_change_alert_threshold:5`).
- `btc.ladder_state` gained arm/confirm columns (confirm_rebound_pct, confirm_days, armed_at,
  armed_on_date, armed_price_usd, low_since_arm_usd, confirm_streak, fire_reason). New private
  `btc.positions` table (RLS on, **no anon policy**). `db/schema.sql` synced (canonical).

## Part 3 — 3-phase state machine (`collector/ladder.py evaluate()`)
`pending → armed → fired`. A tranche **arms** when its `value_rule` is true (records armed price +
`low_since_arm`). While **armed**: a fresh low resets `confirm_streak` (anti-bull-trap); once price
holds `≥ low×(1+rebound%)` for `confirm_days` in a row it **fires** (`fire_reason='confirmed'`).
**Uptrend fallback** (fire-once): when `price > sma_200d` and ≥1 tranche is not fired, all remaining
fire with `fire_reason='uptrend'` and ONE 🚀 VANGNET message. Idempotent. Runs after persist, before
messaging. Returns `{armed,fired,uptrend}` for scheduling.

## Part 4 — action-led messages, € at EVERY trap
Plain tier text (watch="we naderen, nog niet in de koopzone", naderend="dicht bij de koopzone",
sterke_bodem_confluentie="diepe koopzone"; top neutraal="geen verkoopsignaal",
verhit/sterke_top="condities kantelen richting een top"). Digest sections: header (DD/MM/YYYY +
prijs + % onder de top) → Stand van zaken → Wat moet jij nu doen? → Jouw ladder (privé; budget +
per-trap € + status line: pending "wacht op niveau (…)", armed "BEWAPEND op $X, koop > $thr",
fired "KOOP-SIGNAAL date (reason)") + Ingezet/Droog kruit/Gem. instap → Signalen (✅/➖/⚪) →
disclaimer. ⚠️ line when `available < total`. ARMEER/KOOPMOMENT/VANGNET/TOP messages per spec.

## Part 5 — backtest (`--backtest [--years N]`, price-only, HONEST)
Runs the SAME arm→rebound→fire→uptrend logic over Kraken daily closes using ONLY price signals
(on-chain + F&G treated unavailable; score renormalized like live). Disclaimer top + bottom.
3y run today: window starts above the 200-day MA → uptrend fallback fires all 3 at ~$96.560 day-0
(faithful — price-only history can't arm the deep levels); summary compares avg instap vs day-1 and
vs window low ($60.856, +58.7%). No DB writes.

## Part 6 — positions (private)
`--mark-bought <trap|0> <eur> [--price] [--note]` inserts into `btc.positions` (and marks a real
trap fired, `fire_reason='manual'`); `--positions` lists with totals; `--status` shows Ingezet /
Droog kruit / Gem. instap. Verified €100@$62.000 → Ingezet €100, Droog kruit €10.021; test row
deleted, `--positions` back to empty.

## Part 7 — reduced-noise scheduling (`collector/main.py`)
Always computes indicators + runs the ladder + writes the row. Sends a CHANGE alert only on a
meaningful event (tier change, |score Δ| ≥ 5, or a trap armed/fired/uptrend) and only when it isn't
already a digest day. Sends the FULL digest only on `--digest` or weekday == `digest_weekday`
(weekly Monday). Quiet non-event non-digest day → nothing sent (row still written). ARMEER/
KOOPMOMENT/VANGNET always send. GH Actions cron stays daily; the code decides what to send.

## Part 8 — verification
- `--status`, `--preview arm|fire|uptrend --send-test` (all 3 🧪 pings delivered=true, € shown),
  `--backtest --years 3`, `--mark-bought`+`--positions`+cleanup, `--digest` (real, delivered=true),
  `--status` again unchanged (previews/backtest side-effect-free).
- **Supabase security advisor: NO ERROR** (only INFO `rls_enabled_no_policy` on the 3 private
  tables — intended deny-all-to-anon). `has_table_privilege(anon, SELECT)` = false for
  `alerts/ladder_state/positions`, true only for `indicators/latest`.
- Today: **bottom_score 38 (watch, 3/9)**, **top_score 0 (neutraal, 0/7)**, BTC ~$62.8k.
