import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
const schema = (import.meta.env.VITE_DB_SCHEMA as string) || "btc";

// Read-only client scoped to the btc schema (anon key, RLS allows SELECT only).
export const supabase = createClient(url, anonKey, {
  db: { schema },
  auth: { persistSession: false },
});
