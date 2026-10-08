import type { DerivativesRow } from "../types";

const VERDICT_NL: Record<string, { label: string; emoji: string; tone: string }> = {
  geen_daling: { label: "Geen noemenswaardige daling", emoji: "⚪", tone: "text-gray-300" },
  hefboom_flush: { label: "Hefboom-flush (gedwongen verkoop)", emoji: "🧹", tone: "text-emerald-300" },
  spot_verkoop: { label: "Spot-verkoop (echte verkoopdruk)", emoji: "🩸", tone: "text-red-300" },
  shorts_stapelen: { label: "Shorts stapelen zich op", emoji: "🐻", tone: "text-amber-300" },
  gemengd: { label: "Gemengd beeld", emoji: "🌫️", tone: "text-gray-300" },
  onbekend: { label: "Onvoldoende data", emoji: "❔", tone: "text-gray-400" },
};

const nf0 = (v: number) => v.toLocaleString("nl-NL", { maximumFractionDigits: 0 });
const pct = (v: number | null, d = 1) =>
  v == null ? "n.b." : `${v > 0 ? "+" : ""}${v.toFixed(d).replace(".", ",")}%`;
const musd = (v: number | null) => (v == null ? "n.b." : `$${(v / 1e6).toFixed(1).replace(".", ",")}M`);

export default function FlushCard({ row }: { row: DerivativesRow | null }) {
  if (!row) {
    return (
      <div className="rounded-xl bg-panel p-5 text-sm text-gray-400">
        Flush-check: nog geen derivatives-meting.
      </div>
    );
  }
  const v = VERDICT_NL[row.flush_verdict] ?? VERDICT_NL.onbekend;
  const meaning = (row.flush_detail?.meaning_nl as string | undefined) ?? "";
  const liqTotal = (row.liq_long_usd ?? 0) + (row.liq_short_usd ?? 0);

  return (
    <div className="rounded-xl bg-panel p-5">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <div className={`text-lg font-semibold ${v.tone}`}>
          {v.emoji} {v.label}
        </div>
        <div className="text-xs text-gray-500">
          Koers {pct(row.price_chg_24h_pct)} vs vorig dagslot · bron {row.source ?? "n.b."}
        </div>
      </div>
      {meaning && <p className="text-sm text-gray-300 mt-2">{meaning}</p>}

      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 text-sm">
        <div>
          <dt className="text-gray-500">Open interest (24u)</dt>
          <dd className="font-medium">
            {pct(row.oi_change_24h_pct)}
            {row.oi_usd != null && <span className="text-gray-500"> · ${nf0(row.oi_usd / 1e9)}B</span>}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Funding (8u)</dt>
          <dd className="font-medium">{row.funding_rate == null ? "n.b." : pct(row.funding_rate * 100, 4)}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Liquidaties (24u)</dt>
          <dd className="font-medium">
            {row.liq_long_usd == null ? "n.b." : musd(liqTotal)}
            {row.liq_long_share_pct != null && (
              <span className="text-gray-500"> · {row.liq_long_share_pct.toFixed(0)}% longs</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-gray-500">Dekking liquidaties</dt>
          <dd className="font-medium">
            {row.liq_covered_hours == null ? "n.b." : `${row.liq_covered_hours}u / 24u`}
            {row.liq_truncated && <span className="text-amber-400"> ⚠️ afgekapt</span>}
          </dd>
        </div>
      </dl>
      <p className="text-xs text-gray-600 mt-3">
        Heuristiek op perp-positionering (OKX, Bybit-fallback). Analytisch gekalibreerd, niet
        gebacktest. Geen koop-/verkoopsignaal.
      </p>
    </div>
  );
}
