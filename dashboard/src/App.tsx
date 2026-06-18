import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import type { IndicatorRow } from "./types";
import Gauge from "./components/Gauge";
import IndicatorTable from "./components/IndicatorTable";
import ScoreChart from "./components/ScoreChart";

export default function App() {
  const [latest, setLatest] = useState<IndicatorRow | null>(null);
  const [history, setHistory] = useState<{ captured_date: string; bottom_score: number }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const { data: latestRows, error: e1 } = await supabase
          .from("latest")
          .select("*")
          .limit(1);
        if (e1) throw e1;

        const { data: hist, error: e2 } = await supabase
          .from("indicators")
          .select("captured_date,bottom_score")
          .order("captured_date", { ascending: true })
          .limit(180);
        if (e2) throw e2;

        setLatest((latestRows?.[0] as IndicatorRow) ?? null);
        setHistory((hist as { captured_date: string; bottom_score: number }[]) ?? []);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div className="min-h-full max-w-3xl mx-auto px-4 py-6">
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <span className="text-accent">₿</span> BTC Bodem Radar
          </h1>
          <p className="text-gray-400 text-sm">
            Dagelijkse cyclus-bodem monitor — geen financieel advies.
          </p>
        </div>
      </header>

      {loading && <div className="text-gray-400">Laden…</div>}
      {error && (
        <div className="rounded-lg bg-red-900/40 border border-red-700 p-3 text-red-200 text-sm">
          Fout bij laden: {error}
        </div>
      )}

      {latest && (
        <>
          <section className="grid sm:grid-cols-2 gap-4 mb-6">
            <div className="rounded-xl bg-panel p-5 flex flex-col items-center justify-center">
              <Gauge score={latest.bottom_score} tier={latest.tier} />
              <div className="mt-3 text-sm text-gray-400">
                {latest.triggered_count} van {latest.available_count} signalen actief
              </div>
            </div>
            <div className="rounded-xl bg-panel p-5 flex flex-col justify-center gap-2">
              <div className="text-gray-400 text-sm">BTC prijs</div>
              <div className="text-3xl font-bold">
                {latest.price_usd
                  ? `$${latest.price_usd.toLocaleString("nl-NL", { maximumFractionDigits: 0 })}`
                  : "n.b."}
              </div>
              <div className="text-sm text-gray-400">
                Daling vanaf ATH:{" "}
                {latest.drawdown_from_ath_pct != null
                  ? `${latest.drawdown_from_ath_pct.toFixed(1)}%`
                  : "n.b."}
              </div>
              <div className="text-xs text-gray-500 mt-1">Laatste meting: {latest.captured_date}</div>
            </div>
          </section>

          <section className="mb-6">
            <h2 className="text-lg font-semibold mb-2">Indicatoren</h2>
            <IndicatorTable row={latest} />
          </section>

          <section className="mb-6">
            <h2 className="text-lg font-semibold mb-2">Bodemscore over tijd</h2>
            <div className="rounded-xl bg-panel p-3">
              <ScoreChart data={history} />
            </div>
          </section>
        </>
      )}

      <footer className="text-xs text-gray-600 mt-8 border-t border-gray-800 pt-4">
        Monitoringtool, geen financieel advies. Bodems zijn pas achteraf te bevestigen. Deze tool
        plaatst nooit orders en zegt nooit "koop".
      </footer>
    </div>
  );
}
