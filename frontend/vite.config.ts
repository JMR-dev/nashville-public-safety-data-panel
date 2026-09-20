import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.PANEL_API_ORIGIN ?? "http://127.0.0.1:8000";
const proxy = { "/graphql": api, "/events": api };

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", proxy },
  preview: { host: "127.0.0.1", proxy },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
    setupFiles: ["test/setup.ts"],
    restoreMocks: true,
    unstubGlobals: true,
    coverage: {
      provider: "v8",
      // Application code only; tests and their helpers live outside src/.
      include: ["src/**/*.{ts,tsx}"],
      reporter: ["text", "json-summary"],
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
});
