interface Props {
  score: number;
  tier: string;
  title?: string;
}

// Covers both the bottom tiers and the symmetric top tiers.
const TIER_COLORS: Record<string, string> = {
  neutraal: "#9ca3af",
  watch: "#eab308",
  naderend: "#f97316",
  sterke_bodem_confluentie: "#ef4444",
  verhit: "#f97316",
  sterke_top_confluentie: "#ef4444",
};

const TIER_LABELS: Record<string, string> = {
  neutraal: "Neutraal",
  watch: "Waakzaam",
  naderend: "Naderend",
  sterke_bodem_confluentie: "Sterke bodem-confluentie",
  verhit: "Verhit",
  sterke_top_confluentie: "Sterke top-confluentie",
};

export default function Gauge({ score, tier, title }: Props) {
  const color = TIER_COLORS[tier] ?? "#9ca3af";
  const radius = 80;
  const circumference = Math.PI * radius; // semicircle
  const clamped = Math.max(0, Math.min(100, score));
  const offset = circumference * (1 - clamped / 100);

  return (
    <div className="flex flex-col items-center">
      {title && <div className="text-sm font-semibold text-gray-300 mb-1">{title}</div>}
      <svg width="220" height="130" viewBox="0 0 220 130">
        <path
          d="M 20 120 A 90 90 0 0 1 200 120"
          fill="none"
          stroke="#222a3a"
          strokeWidth="16"
          strokeLinecap="round"
        />
        <path
          d="M 20 120 A 90 90 0 0 1 200 120"
          fill="none"
          stroke={color}
          strokeWidth="16"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 0.6s ease" }}
        />
        <text x="110" y="100" textAnchor="middle" fontSize="40" fontWeight="700" fill="#e6e9ef">
          {score}
        </text>
        <text x="110" y="120" textAnchor="middle" fontSize="12" fill="#9ca3af">
          / 100
        </text>
      </svg>
      <div className="mt-2 text-lg font-semibold" style={{ color }}>
        {TIER_LABELS[tier] ?? tier}
      </div>
    </div>
  );
}
