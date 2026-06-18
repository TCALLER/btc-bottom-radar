import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Legend } from "recharts";

interface Point {
  captured_date: string;
  bottom_score: number;
  top_score?: number;
}

export default function ScoreChart({ data }: { data: Point[] }) {
  if (!data.length) {
    return <div className="text-gray-500 text-sm p-4">Nog geen historiek beschikbaar.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 10, right: 16, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id="bottomFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22c55e" stopOpacity={0.45} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="topFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.4} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#222a3a" />
        <XAxis dataKey="captured_date" stroke="#6b7280" fontSize={11} />
        <YAxis domain={[0, 100]} stroke="#6b7280" fontSize={11} />
        <Tooltip
          contentStyle={{ background: "#141925", border: "1px solid #222a3a", color: "#e6e9ef" }}
          labelStyle={{ color: "#9ca3af" }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area
          type="monotone"
          dataKey="bottom_score"
          stroke="#22c55e"
          strokeWidth={2}
          fill="url(#bottomFill)"
          name="Bodemscore"
        />
        <Area
          type="monotone"
          dataKey="top_score"
          stroke="#ef4444"
          strokeWidth={2}
          fill="url(#topFill)"
          name="Topscore"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
