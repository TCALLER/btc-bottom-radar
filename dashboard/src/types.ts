export interface IndicatorDetail {
  value: number | boolean | null;
  threshold: string | number;
  triggered: boolean;
  available: boolean;
  detail: Record<string, unknown>;
}

export interface IndicatorRow {
  captured_date: string;
  price_usd: number | null;
  all_time_high_usd: number | null;
  drawdown_from_ath_pct: number | null;
  mayer_multiple: number | null;
  rsi_14d: number | null;
  ma_200w: number | null;
  pi_cycle_bottom: boolean | null;
  fear_greed: number | null;
  mvrv_zscore: number | null;
  sopr: number | null;
  supply_profit_pct: number | null;
  bottom_score: number;
  tier: string;
  triggered_count: number;
  available_count: number;
  signals_triggered: string[];
  indicators_detail: Record<string, IndicatorDetail>;
}
