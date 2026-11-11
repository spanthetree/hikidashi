/// <reference types="vitest/config" />
import { paraglideVitePlugin } from "@inlang/paraglide-js";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

// `npm run dev` beside `uvicorn app.api.main:app --port 8080`: the API and the
// media files come from FastAPI, everything else from Vite with hot reload.
const backend = process.env.HIKIDASHI_API ?? "http://localhost:8080";

export default defineConfig({
  plugins: [
    // messages/*.json compile to typed functions in src/lib/paraglide. The
    // locale is a choice saved in this browser (src/lib/i18n.ts), else the
    // browser's languages, else English; never the URL. `npm run i18n`
    // compiles the same, for svelte-check.
    paraglideVitePlugin({
      project: "./project.inlang",
      outdir: "./src/lib/paraglide",
      strategy: ["custom-saved", "preferredLanguage", "baseLocale"],
    }),
    sveltekit(),
  ],
  test: {
    // Only the unit tests: e2e/ is Playwright's (npm run e2e).
    include: ["src/**/*.test.ts"],
    // `npm run coverage`. Report only, no thresholds: it shows where the
    // tests are thin, it does not fail the build.
    coverage: {
      provider: "v8",
      include: ["src/lib/**/*.{ts,svelte}"],
      exclude: ["src/lib/paraglide/**", "src/**/*.test.ts"],
      reporter: ["text-summary", "text", "html"],
    },
  },
  server: {
    proxy: {
      "/api": backend,
      "/media": backend,
    },
  },
});
