import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // Plain static files that FastAPI serves from app/web. Every route is
    // rendered in the browser, so one index.html answers them all.
    adapter: adapter({
      pages: "../app/web",
      assets: "../app/web",
      fallback: "index.html",
      strict: false,
    }),
    // svelte-check type-checks the Playwright tests too.
    typescript: {
      config: (config) => {
        config.include.push("../e2e/**/*.ts", "../playwright.config.ts");
      },
    },
  },
};
