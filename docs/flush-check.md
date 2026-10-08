# Flush-check: "hefboom-flush of spot-verkoop?" — documentatie (NotebookLM)

*Versie 1.0 — 08/10/2026 — status: gebouwd en unit-getest, eerste live run nog te bewijzen.*

## 1. Doel

De BTC Bottom Radar meet dagelijks **hoe ver** de markt van een cyclusbodem staat. Deze uitbreiding
beantwoordt een andere vraag: als de koers zakt, **wie verkocht er?**

- **Hefboom-flush**: gehefboomde longs werden geliquideerd. Open interest (OI) verdampt, funding
  valt terug naar nul of negatief, de liquidaties zijn overwegend longs. De verkopers waren
  *gedwongen*, niet overtuigd. Zo'n flush put zichzelf meestal snel uit.
- **Spot-verkoop**: holders verkochten echte coins. OI blijft intact, funding blijft positief: de
  hefboom zit nog in het systeem en kan later alsnog gewassen worden → vervolgrisico.
- **Shorts stapelen**: OI stijgt terwijl funding negatief is: nieuwe shorts openen in de daling.
  Dat is squeeze-brandstof, geen bodembewijs.

Wat het **niet** is: een koop- of verkoopsignaal, en geen voorspelling van de richting erna. Het
is een heuristiek op positionering, analytisch gekalibreerd, niet gebacktest.

## 2. Architectuur

```
GitHub Actions (daily.yml, 05:30 UTC)
  └─ collector.main
       ├─ (bestaand) prijs/on-chain → btc.indicators
       └─ (nieuw) derivatives.fetch_snapshot ──► flush.classify ──► btc.derivatives
              │   OKX public API (primair)              pure functie      │
              │   Bybit public API (fallback)           11 unit tests     ▼
              │                                                   btc.latest_derivatives (view)
              ▼                                                           │
        Telegram digest-sectie + CHANGE-alert                      dashboard FlushCard
```

Vereisten vooraf: de bestaande radar draait (Supabase-project `btc-bottom-radar`, schema `btc`,
GitHub-secrets gezet). Er zijn **geen nieuwe secrets** nodig: OKX en Bybit zijn publiek.

## 3. Stapsgewijze opbouw

1. **Config** — `config/thresholds.json`, blok `derivatives`:
   `price_drop_pct 2.0` (vanaf −2% stellen we de vraag) · `oi_flush_drop_pct 4.0` (OI −4% in
   24u = deleveraging) · `oi_stable_band_pct 1.5` · `funding_negative 0.0` · `funding_hot 0.0001`
   (0,01%/8u) · `long_liq_dominance_pct 65` · `okx_ct_val_btc 0.01` · `liq_max_pages 20`.
2. **Databron** — `collector/derivatives.py`:
   - `okx_funding()` → `public/funding-rate?instId=BTC-USDT-SWAP` → `fundingRate`, `premium`.
   - `okx_open_interest_now()` → `public/open-interest` → `oiUsd`, `oiCcy`.
   - `okx_open_interest_24h_ago()` → `rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1H`
     → rij `[ts, oi_usd, vol_usd]` die het dichtst bij nu−24u ligt; de werkelijke leeftijd gaat
     mee als `oi_ref_age_hours` (de reeks kan tot ~1 dag achterlopen).
   - `okx_liquidations_24h()` → `public/liquidation-orders?instType=SWAP&uly=BTC-USDT&state=filled`,
     100 orders per pagina, cursor `after=<ts>`, max `liq_max_pages`. USD per order =
     `sz × 0.01 BTC × bkPx`. Som per `posSide` (long/short). Bij afkappen: `liq_truncated=true`
     en `liq_covered_hours`. Bron down → `None` (nooit 0).
   - Fallback Bybit (`market/tickers`, `market/open-interest`) voor funding + OI; geen liquidaties.
   - `fetch_snapshot(cfg, price_usd)` bundelt alles en vult `missing[]`.
3. **Oordeel** — `collector/flush.py::classify(price_chg_pct, snap, cfg)`:
   ```
   koers > −drop                       → geen_daling
   OI én funding ontbreken             → onbekend
   OI ≤ −4% én (funding ≤ 0,01% óf longs ≥ 65% van liquidaties óf liq onbekend) → hefboom_flush
   OI > +1,5% én funding ≤ 0           → shorts_stapelen
   OI ≥ −1,5% én funding > 0           → spot_verkoop
   anders                              → gemengd
   ```
   Output: `verdict, emoji, label_nl, meaning_nl, reasons[], inputs{}`.
4. **Opslag** — `db/schema.sql`: tabel `btc.derivatives` (unieke `captured_date`), RLS aan,
   anon-SELECT-policy (marktdata, niets persoonlijks), view `btc.latest_derivatives`
   (`security_invoker = true`). Na elke kolomwijziging de view opnieuw aanmaken uit dit bestand.
   `collector/persist_supabase.py`: `upsert_derivatives`, `fetch_last_derivatives`.
5. **Integratie** — `collector/main.py`: na de upsert van `btc.indicators` draait de flush-check in
   een try/except (fout = log + `onbekend`, de dagrun breekt nooit). Een **oordeelwissel op een
   dalingsdag** telt als `meaningful` → CHANGE-alert.
6. **Telegram** — `notify_telegram.py::format_flush_section()`: sectie "🧹 Daling: hefboom of
   spot?" in de digest (na "Wat moet jij nu doen?"), één regel op rustige dagen; de CHANGE-alert
   krijgt de regel `Daling = <oordeel>` + de betekenis.
7. **Dashboard** — `dashboard/src/components/FlushCard.tsx`, gevoed via `latest_derivatives`
   (anon). Toont oordeel, betekenis, OI Δ24u, funding, liquidaties + dekking, caveat. Ontbrekende
   view breekt de pagina niet.

## 4. Configuratie / credentials

Geen nieuwe. Bestaande verwijzingen: Supabase service-key en Telegram-token in GitHub-secrets en
1Password (vault "Projecten", items van de radar). Nooit in de repo.

## 5. Testprocedure

1. `python -m pytest -q` → 17 tests groen (6 TA + 11 flush/liquidatie-aggregatie, offline).
2. `cd dashboard && npx tsc --noEmit && npm run build` → groen.
3. Schema: `db/schema.sql` toepassen (idempotent) → `select * from btc.latest_derivatives` geeft
   0 rijen zonder fout.
4. **Eerste live bewijs** (kan niet vanuit de cloud-bouwsessie, exchange-egress geblokkeerd):
   GitHub → Actions → "BTC Bottom Radar — daily" → *Run workflow*. Verwacht in de log:
   `derivatives: src=okx funding=… oiΔ24h=…% liqL=… liqS=… missing=[]` en
   `upserted derivatives row for <datum> (verdict=…)`. Daarna de digest in Telegram met de sectie.
   [SCREENSHOT: Actions-log met de derivatives-regel] [SCREENSHOT: Telegram-digest met 🧹-sectie]
5. Lokaal alternatief op de Dell: `python -m collector.derivatives` print één snapshot zonder
   DB-write.

## 6. Bekende valkuilen

- **OKX rubik-OI-reeks loopt achter** (tot ~1 dag gezien op 08/10). Daarom `oi_ref_age_hours`
  in de rij; als die > 30 is het "24u-verschil" eigenlijk een langer venster.
- **Liquidatiefeed is per 100 orders**; op een crashdag overschrijdt 24u de 2.000-ordergrens →
  `liq_truncated`. Verhoog `liq_max_pages` enkel met oog op de OKX-limiet (5 req/2 s).
- **Geo-blocks**: Binance 451 vanaf US-runners; Bybit kán ook blokkeren → dan `onbekend`, nooit
  een crash.
- **Daily-snapshot ≠ realtime**: de run om 07:30 beschrijft gisteren. Voor "wat gebeurt er nú"
  is een tweede cron of een `/flush`-botcommando nodig (bewust nog niet gebouwd).
- **Heuristiek**: drempels zijn analytisch gekozen. Bij structureel foute oordelen de drempels in
  config bijstellen, niet de code; en de wijziging dateren in DECISIONS.md.
