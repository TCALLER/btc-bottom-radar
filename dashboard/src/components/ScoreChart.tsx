import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Point {
  captured_date: string;
  bottom_score: number;
}

export default function ScoreChart({ data }: { data: Point[] }) {
  if (!data.length) {
    return <div className="text-gray-500 text-sm p-4">Nog geen historiek beschikbaar.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 10, right: 16, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f7931a" stopOpacity={0.5} />
            <stop offset="95%" stopColor="#f7931a" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#222a3a" />
        <XAxis dataKey="captured_date" stroke="#6b7280" fontSize={11} />
        <YAxis domain={[0, 100]} stroke="#6b7280" fontSize={11} />
        <Tooltip
          contentStyle={{ background: "#141925", border: "1px solid #222a3a", color: "#e6e9ef" }}
          labelStyle={{ color: "#9ca3af" }}
        />
        <Area
          type="monotone"
          dataKey="bottom_score"
          stroke="#f7931a"
          strokeWidth={2}
          fill="url(#scoreFill)"
          name="Bodemscore"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
