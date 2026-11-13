/// <reference types="vitest/config" />
import { paraglideVitePlugin } from "@inlang/paraglide-js";
import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { defineConfig, type Plugin } from "vite";

/* The API explorer's Swagger UI (#147), served from the build, never a CDN:
   app/api/explorer.py's page loads /explorer/swagger-ui-bundle.js and .css.
   Copied into static/ (git-ignored) before a build, so the build carries it. */
function explorer(): Plugin {
  return {
    name: "hikidashi-explorer",
    buildStart() {
      const dist = dirname(createRequire(import.meta.url).resolve("swagger-ui-dist/package.json"));
      const out = join("static", "explorer");
      mkdirSync(out, { recursive: true });
      for (const f of ["swagger-ui-bundle.js", "swagger-ui.css", "LICENSE"]) {
        copyFileSync(join(dist, f), join(out, f));
      }
    },
  };
}

// `npm run dev` beside `uvicorn app.api.main:app --port 8080`: the API and the
// media files come from FastAPI, everything else from Vite with hot reload.
const backend = process.env.HIKIDASHI_API ?? "http://localhost:8080";

export default defineConfig({
  plugins: [
    explorer(),
    // messages/*.json compile to typed functions in src/lib/paraglide. The
    // locale is a choice saved in this browser (src/lib/i18n.ts), else the
    // browser's languages, else English; never the URL. `npm run i18n`
    // compiles the same, for svelte-check.
    paraglideVitePlugin({
      project: "./project.inlang",
      outdir: "./src/lib/paraglide",
      strategy: ["custom-saved", "preferredLanguage", "baseLocale"],
    }),
    // SvelteKit's own options, here since SvelteKit 3 (no svelte.config.js).
    sveltekit({
      preprocess: vitePreprocess(),
      // Plain static files that FastAPI serves from app/web. Every route is
      // rendered in the browser, so one index.html answers them all.
      adapter: adapter({
        pages: "../app/web",
        assets: "../app/web",
        fallback: "index.html",
        strict: false,
      }),
    }),
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
