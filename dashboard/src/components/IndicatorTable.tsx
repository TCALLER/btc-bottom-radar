import type { IndicatorRow } from "../types";

const LABELS_NL: Record<string, string> = {
  // bottom radar
  pi_cycle_bottom: "Pi-Cycle Bodem",
  ma_200w: "200-weken MA",
  mayer_multiple: "Mayer Multiple",
  rsi_14d: "RSI (14d)",
  drawdown_from_ath_pct: "Daling vanaf ATH",
  fear_greed: "Fear & Greed",
  mvrv_zscore: "MVRV Z-Score",
  sopr: "SOPR",
  supply_profit_pct: "Supply in winst %",
  // top radar
  pi_cycle_top: "Pi-Cycle Top",
  mvrv_zscore_high: "MVRV Z-Score (hoog)",
  mayer_high: "Mayer Multiple (hoog)",
  rsi_14w_high: "RSI (weekly, hoog)",
  fear_greed_high: "Fear & Greed (hebzucht)",
  nupl: "NUPL",
  puell_multiple: "Puell Multiple",
};

export const BOTTOM_KEYS = [
  "pi_cycle_bottom", "ma_200w", "mayer_multiple", "rsi_14d",
  "drawdown_from_ath_pct", "fear_greed", "mvrv_zscore", "sopr", "supply_profit_pct",
];

export const TOP_KEYS = [
  "pi_cycle_top", "mvrv_zscore_high", "mayer_high", "rsi_14w_high",
  "fear_greed_high", "nupl", "puell_multiple",
];

function fmtValue(key: string, v: number | boolean | null): string {
  if (v === null || v === undefined) return "n.b.";
  if (typeof v === "boolean") return v ? "actief" : "niet actief";
  switch (key) {
    case "ma_200w":
      return `$${v.toLocaleString("nl-NL", { maximumFractionDigits: 0 })}`;
    case "drawdown_from_ath_pct":
    case "supply_profit_pct":
      return `${v.toFixed(1)}%`;
    case "mayer_multiple":
    case "mayer_high":
    case "sopr":
    case "mvrv_zscore":
    case "mvrv_zscore_high":
    case "nupl":
    case "puell_multiple":
      return v.toFixed(3);
    case "rsi_14d":
    case "rsi_14w_high":
      return v.toFixed(1);
    default:
      return String(v);
  }
}

export default function IndicatorTable({ row, keys }: { row: IndicatorRow; keys: string[] }) {
  const detail = row.indicators_detail || {};
  return (
    <div className="overflow-x-auto rounded-xl bg-panel p-1">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="px-3 py-2">Indicator</th>
            <th className="px-3 py-2">Waarde</th>
            <th className="px-3 py-2">Drempel</th>
            <th className="px-3 py-2 text-center">Actief</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => {
            const d = detail[key];
            const available = d ? d.available : false;
            const triggered = d ? d.triggered : false;
            const value = d ? (d.value as number | boolean | null) : null;
            const threshold = d ? d.threshold : "—";
            return (
              <tr key={key} className="border-t border-gray-800">
                <td className="px-3 py-2">{LABELS_NL[key] ?? key}</td>
                <td className="px-3 py-2">
                  {available ? fmtValue(key, value) : <span className="text-gray-500">niet beschikbaar</span>}
                </td>
                <td className="px-3 py-2 text-gray-400">{String(threshold)}</td>
                <td className="px-3 py-2 text-center">
                  {!available ? "⚪" : triggered ? "✅" : "➖"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
