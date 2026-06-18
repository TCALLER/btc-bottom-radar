import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base is "/" for local preview / Cloudflare / custom domains, but a GitHub
// Pages *project* site is served under "/<repo>/" — set VITE_BASE at build time.
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react()],
});
