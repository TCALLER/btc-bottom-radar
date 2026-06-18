// BTC Bottom Radar — interactive Telegram bot (read-only listener).
//
// Public HTTPS endpoint called by Telegram's webhook. NOTIFY-ONLY: it only reads
// the btc tables and replies; it never trades and never issues buy/sell orders.
//
// SECURITY (all three required):
//   1. header x-telegram-bot-api-secret-token must equal TELEGRAM_WEBHOOK_SECRET (else 401)
//   2. only acts on chat.id == ALLOWED_CHAT_ID (any other chat -> 200, silent)
//   3. secrets come from Edge Function env, never the repo
// Deployed with JWT verification disabled (Telegram can't send a Supabase JWT);
// the secret-token header + chat-id allowlist are the gate.

const BOT_TOKEN = Deno.env.get("TELEGRAM_BOT_TOKEN") ?? "";
const WEBHOOK_SECRET = Deno.env.get("TELEGRAM_WEBHOOK_SECRET") ?? "";
const ALLOWED_CHAT_ID = Deno.env.get("ALLOWED_CHAT_ID") ?? "";
const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

const BOTTOM_TIER: Record<string, string> = {
  neutraal: "nog rustig, geen bodem in zicht",
  watch: "we naderen, nog niet in de koopzone",
  naderend: "dicht bij de koopzone",
  sterke_bodem_confluentie: "diepe koopzone",
};
const TOP_TIER: Record<string, string> = {
  neutraal: "geen verkoopsignaal",
  watch: "licht verhoogd",
  verhit: "condities richting een top",
  sterke_top_confluentie: "condities richting een top",
};

// ---- formatting (NL) ----
function grp(n: number): string {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}
function usd(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? "$" + grp(n) : "n.b.";
}
function eur(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? grp(n) : "n.b.";
}
function pct(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(1).replace(".", ",") + "%" : "n.b.";
}
function dmy(iso: string | null | undefined): string {
  if (!iso || iso.length < 10) return iso ?? "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}
function num(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// ---- btc schema reads (service role, schema btc) ----
async function btc(path: string): Promise<any> {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    headers: {
      apikey: SERVICE_KEY,
      Authorization: `Bearer ${SERVICE_KEY}`,
      "Accept-Profile": "btc",
    },
  });
  if (!res.ok) throw new Error(`btc read ${path}: ${res.status}`);
  return await res.json();
}

async function sendMessage(text: string): Promise<void> {
  await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: ALLOWED_CHAT_ID,
      text,
      parse_mode: "HTML",
      disable_web_page_preview: true,
    }),
  });
}

// ---- ladder helpers ----
function nearestTrap(ladder: any[]): any | null {
  const open = ladder.filter((t) => t.status !== "fired").sort((a, b) => a.tranche_id - b.tranche_id);
  return open.length ? open[0] : null;
}
function trapCondition(tid: number, row: any): string {
  const s = num(row.sma_200d);
  if (tid === 1) return `koers ≤ 200w-MA (${usd(row.ma_200w)})`;
  if (tid === 2) {
    return `koers ≤ 0,70·SMA200d (${usd(s !== null ? 0.7 * s : null)}), of bodemscore ≥ 62`;
  }
  if (tid === 3) {
    return `capitulatie: koers ≤ 0,50·SMA200d (${usd(s !== null ? 0.5 * s : null)}), of MVRV-Z ≤ 0,1, of F&amp;G ≤ 10`;
  }
  return "";
}
function threshold(tr: any): number | null {
  const low = num(tr.low_since_arm_usd);
  const r = num(tr.confirm_rebound_pct);
  return low !== null && r !== null ? low * (1 + r / 100) : null;
}

// ---- command handlers ----
async function cmdHelp(): Promise<string> {
  return [
    "🤖 <b>BTC Radar bot</b> — alleen-lezen, handelt nooit.",
    "/btc — live koers vs dagmeting &amp; jouw ladder-niveaus",
    "/radar — prijs, bodem- &amp; topscore, dichtstbijzijnde trap",
    "/ladder — jouw ladder (privé): status + € per trap",
    "/positions — geregistreerde aankopen + totalen",
    "/digest — de laatste opgeslagen digest",
    "/help — deze lijst",
  ].join("\n");
}

async function cmdRadar(): Promise<string> {
  const rows = await btc("latest?select=*");
  if (!rows.length) return "Nog geen meting beschikbaar.";
  const r = rows[0];
  const ladder = await btc("ladder_state?select=*");
  const nt = nearestTrap(ladder);
  const lines = [
    `📊 <b>BTC Radar</b> — ${dmy(r.captured_date)}`,
    `Prijs: ${usd(r.price_usd)} · ${pct(r.drawdown_from_ath_pct)} onder ATH`,
    `Bodem: ${r.bottom_score}/100 (${BOTTOM_TIER[r.tier] ?? r.tier})`,
    `Top: ${r.top_score}/100 (${TOP_TIER[r.top_tier] ?? r.top_tier})`,
  ];
  if (nt) {
    lines.push(`🎯 Dichtstbij: ${nt.label} (~€${eur(nt.amount_eur)}) — ${trapCondition(nt.tranche_id, r)}`);
  } else {
    lines.push("🎯 Alle trappen afgehandeld.");
  }
  return lines.join("\n");
}

async function ladderTotals(ladder: any[]) {
  const budget = ladder.reduce((s, t) => s + (num(t.amount_eur) ?? 0), 0);
  const pos = await btc("positions?select=amount_eur,price_usd");
  const deployed = pos.reduce((s: number, p: any) => s + (num(p.amount_eur) ?? 0), 0);
  const priced = pos.filter((p: any) => num(p.price_usd) !== null);
  const wsum = priced.reduce((s: number, p: any) => s + (num(p.amount_eur) ?? 0), 0);
  const avg = wsum ? priced.reduce((s: number, p: any) => s + num(p.amount_eur)! * num(p.price_usd)!, 0) / wsum : null;
  return { budget, deployed, avg };
}

async function cmdLadder(): Promise<string> {
  const ladder = (await btc("ladder_state?select=*")).sort((a: any, b: any) => a.tranche_id - b.tranche_id);
  if (!ladder.length) return "Ladder nog niet geïnitialiseerd.";
  const rows = await btc("latest?select=*");
  const r = rows.length ? rows[0] : {};
  const { budget, deployed, avg } = await ladderTotals(ladder);
  const lines = [`🪜 <b>Jouw ladder (privé)</b> — budget €${eur(budget)}`];
  for (const t of ladder) {
    let detail: string;
    if (t.status === "armed") {
      detail = `BEWAPEND op ${usd(t.armed_price_usd)}, koop &gt; ${usd(threshold(t))}`;
    } else if (t.status === "fired") {
      detail = `KOOP-SIGNAAL ${dmy(t.fired_on_date)} (${t.fire_reason ?? "—"})`;
    } else {
      detail = `wacht op niveau (${trapCondition(t.tranche_id, r)})`;
    }
    lines.push(`${t.tranche_id}) ~€${eur(t.amount_eur)} — ${detail}`);
  }
  const avgTxt = avg !== null ? usd(avg) : "n.v.t.";
  lines.push(`Ingezet: €${eur(deployed)} · Droog kruit: €${eur(budget - deployed)} · Gem. instap ${avgTxt}`);
  return lines.join("\n");
}

async function cmdPositions(): Promise<string> {
  const pos = await btc("positions?select=*&order=bought_on.asc");
  if (!pos.length) return "(nog geen aankopen)";
  const ladder = await btc("ladder_state?select=amount_eur");
  const budget = ladder.reduce((s: number, t: any) => s + (num(t.amount_eur) ?? 0), 0);
  const lines = ["💼 <b>Aankopen (privé)</b>"];
  let deployed = 0;
  for (const p of pos) {
    deployed += num(p.amount_eur) ?? 0;
    const trap = p.tranche_id ? `trap ${p.tranche_id}` : "los";
    const price = num(p.price_usd) !== null ? ` @ ${usd(p.price_usd)}` : "";
    const note = p.note ? ` · ${p.note}` : "";
    lines.push(`${dmy(p.bought_on)} · ${trap} · €${eur(p.amount_eur)}${price}${note}`);
  }
  lines.push(`Ingezet: €${eur(deployed)} · Droog kruit: €${eur(budget - deployed)}`);
  return lines.join("\n");
}

function pctSigned(n: number): string {
  return (n >= 0 ? "+" : "−") + Math.abs(n).toFixed(1).replace(".", ",") + "%";
}

async function cmdBtc(): Promise<string> {
  // LIVE price from Kraken public ticker (no key). Never fake on failure.
  let live: number | null = null;
  let change: number | null = null;
  try {
    const res = await fetch("https://api.kraken.com/0/public/Ticker?pair=XBTUSD");
    const j = await res.json();
    if (j && (!Array.isArray(j.error) || j.error.length === 0) && j.result) {
      const k = j.result.XXBTZUSD ?? j.result[Object.keys(j.result)[0]];
      const c = num(k?.c?.[0]);
      const o = num(Array.isArray(k?.o) ? k.o[0] : k?.o);
      if (c !== null) live = c;
      if (c !== null && o !== null && o !== 0) change = ((c - o) / o) * 100;
    }
  } catch (_) {
    live = null;
  }

  const rows = await btc("latest?select=*");
  const r = rows.length ? rows[0] : {};
  const now = new Date().toLocaleTimeString("nl-BE", {
    hour: "2-digit", minute: "2-digit", timeZone: "Europe/Brussels",
  });
  const snap = num(r.price_usd);
  const ma = num(r.ma_200w);
  const sma = num(r.sma_200d);
  const t2 = sma !== null ? 0.70 * sma : null;  // Trap 2 level (0.70·SMA200d)
  const t3 = sma !== null ? 0.50 * sma : null;  // Trap 3 level (0.50·SMA200d)

  const lines = [`₿ <b>BTC live</b> — ${now}`];
  if (live !== null) {
    lines.push(`Live: ${usd(live)}${change !== null ? ` (${pctSigned(change)} / 24u)` : ""}`);
  } else {
    lines.push("Live koers tijdelijk niet beschikbaar.");
  }
  lines.push(`Dagmeting: ${usd(snap)} (${dmy(r.captured_date).slice(0, 5)})`);

  const ref = live !== null ? live : snap;  // compare vs live; fall back to the snapshot
  if (ref !== null && ma !== null) {
    const d = ((ref - ma) / ma) * 100;
    lines.push(`200w-MA: ${usd(ma)} → live ${pctSigned(d)} ${d >= 0 ? "erboven" : "eronder"}`);
  }
  if (ref !== null && t2 !== null) {
    const d = ((ref - t2) / t2) * 100;
    lines.push(`Trap 2 (~${usd(t2)}): ${pctSigned(d)} ${d >= 0 ? "hoger" : "lager"}`);
  }
  if (ref !== null && t3 !== null) {
    const d = ((ref - t3) / t3) * 100;
    lines.push(`Trap 3 capitulatie (~${usd(t3)}): ${Math.abs(d) <= 10 ? "dichtbij" : "nog ver"}`);
  }
  return lines.join("\n");
}

async function cmdDigest(): Promise<string> {
  const rows = await btc("alerts?select=message,sent_at&alert_type=eq.digest&order=sent_at.desc&limit=1");
  if (!rows.length) return "Nog geen digest opgeslagen.";
  return `(laatste digest — ${dmy(rows[0].sent_at)})\n\n${rows[0].message}`;
}

// ---- HTTP entry ----
Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return new Response("ok", { status: 200 });

  // 1) secret-token header
  if (req.headers.get("x-telegram-bot-api-secret-token") !== WEBHOOK_SECRET) {
    return new Response("unauthorized", { status: 401 });
  }

  let update: any;
  try {
    update = await req.json();
  } catch {
    return new Response("ok", { status: 200 });
  }

  const msg = update?.message;
  const chatId = msg?.chat?.id;

  // 2) chat-id allowlist (silent ignore for anyone else)
  if (String(chatId) !== ALLOWED_CHAT_ID) {
    return new Response("ok", { status: 200 });
  }

  const text: string = (msg?.text ?? "").trim();
  const cmd = text.split(/\s+/)[0].toLowerCase().replace(/@.*$/, "");

  try {
    let reply: string;
    switch (cmd) {
      case "/help":
      case "/start":
        reply = await cmdHelp();
        break;
      case "/btc":
        reply = await cmdBtc();
        break;
      case "/radar":
        reply = await cmdRadar();
        break;
      case "/ladder":
        reply = await cmdLadder();
        break;
      case "/positions":
        reply = await cmdPositions();
        break;
      case "/digest":
        reply = await cmdDigest();
        break;
      default:
        reply = "Onbekend commando. Gebruik /help voor de lijst.";
    }
    await sendMessage(reply);
  } catch (e) {
    await sendMessage(`⚠️ Fout bij verwerken: ${String(e)}`);
  }
  return new Response("ok", { status: 200 });
});
