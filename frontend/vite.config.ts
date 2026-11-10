/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  // Hosted builds (Vercel sets VERCEL=1) must point at the HTTPS API; otherwise API calls hit the SPA fallback.
  if (process.env.VERCEL && !env.VITE_API_BASE_URL?.startsWith("https://")) {
    throw new Error("VITE_API_BASE_URL must be set to the https:// API origin for hosted builds");
  }
  return {
    plugins: [react(), tailwindcss()],
    server: { port: 5173, host: "127.0.0.1" },
    build: { sourcemap: false, chunkSizeWarningLimit: 900 },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      css: false,
    },
  };
});
