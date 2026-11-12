/* The stills in the docs, taken on the demo library (#89).

     uv run python scripts/demo_library.py serve     # in the repository, left running
     node scripts/screenshots.mjs                    # here: ../docs/demo/*.webp

   Headless Chromium at 1280x800, in the same Windows fonts as the recordings
   (demo-common.mjs). Each still is a PNG from Playwright, turned into WebP by
   ffmpeg (libwebp). themes.webp is four stills tiled by ffmpeg. Like the
   recordings, they are not checked in CI: run this again when the UI moves. */

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { BASE, fontconfig, session } from "./demo-common.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(resolve(HERE, "../.."), "docs/demo");
const [W, H] = [1280, 800];
const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));

const work = mkdtempSync(join(tmpdir(), "hk-shots-"));
const fonts = fontconfig(mkdtempSync(join(tmpdir(), "hk-fonts-")));
const browser = await chromium.launch({ env: { ...process.env, ...fonts } });
const state = await session();

/** A page in a fresh context, with the look set before the app loads. */
async function open(path, { scheme = "dark", theme = "hikidashi", pixel = "off", locale = "en-GB" } = {}) {
  const ctx = await browser.newContext({
    storageState: state, viewport: { width: W, height: H }, colorScheme: scheme, locale,
    timezoneId: "Asia/Tokyo", reducedMotion: "reduce",
  });
  await ctx.addInitScript(([t, s, p]) => {
    localStorage.setItem("theme", t);
    localStorage.setItem("mode", s);
    localStorage.setItem("pixel", p);
  }, [theme, scheme, pixel]);
  const page = await ctx.newPage();
  await page.goto(BASE + path);
  await page.locator(".thumb").first().waitFor().catch(() => {});
  await sleep(1.2);
  return page;
}

function webp(png, name, quality = 82) {
  const r = spawnSync("ffmpeg", ["-loglevel", "error", "-y", "-i", png, "-c:v", "libwebp",
    "-quality", String(quality), join(OUT, name)]);
  if (r.status !== 0) throw new Error(`ffmpeg: ${r.stderr}`);
  console.log(`  wrote docs/demo/${name}`);
}

async function still(page, name) {
  const png = join(work, name.replace(/\.webp$/, ".png"));
  await page.screenshot({ path: png });
  await page.context().close();
  webp(png, name);
  return png;
}

const SHOTS = {
  /* A reel open: the video beside its caption, transcript, summary and tags. */
  async sheet() {
    const page = await open("/?q=lathe");
    await page.locator(".thumb").first().click();
    await page.getByRole("tab", { name: "Details" }).click().catch(() => {});
    await page.getByText("Transcript", { exact: true }).click().catch(() => {});
    await sleep(1);
    await still(page, "sheet.webp");
  },
  /* A recipe, in light: the checklist and the steps beside the video. */
  async recipe() {
    const page = await open("/?view=recipes", { scheme: "light" });
    await page.locator(".thumb").first().click();
    await sleep(1.2);
    await still(page, "recipe.webp");
  },
  /* The import box, as a new library opens on it. */
  async import() {
    const page = await open("/", { scheme: "light" });
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await sleep(0.8);
    await still(page, "import.webp");
  },
  /* The pipeline page: each worker, and every stage's counts. */
  async pipeline() {
    const page = await open("/pipeline");
    await sleep(1);
    await still(page, "pipeline.webp");
  },
  /* The UI in Japanese, from the browser's language. */
  async japanese() {
    const page = await open("/?view=recipes", { scheme: "light", locale: "ja-JP" });
    await still(page, "japanese.webp");
  },
  /* The four themes, tiled: one light, one dark, one in pixel art. */
  async themes() {
    const looks = [
      { theme: "hikidashi", scheme: "light" },
      { theme: "cyberpunk", scheme: "dark" },
      { theme: "retrowave", scheme: "dark", pixel: "on" },
      { theme: "retrofuture", scheme: "light" },
    ];
    const pngs = [];
    for (const look of looks) {
      const page = await open("/", look);
      const png = join(work, `theme-${look.theme}.png`);
      await page.screenshot({ path: png });
      await page.context().close();
      pngs.push(png);
    }
    const tiled = join(work, "themes.png");
    const r = spawnSync("ffmpeg", ["-loglevel", "error", "-y", ...pngs.flatMap((p) => ["-i", p]),
      "-filter_complex", "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,scale=1280:-1",
      tiled]);
    if (r.status !== 0) throw new Error(`ffmpeg: ${r.stderr}`);
    webp(tiled, "themes.webp");
  },
};

const asked = process.argv.slice(2);
for (const name of asked.length ? asked : Object.keys(SHOTS)) {
  if (!SHOTS[name]) throw new Error(`no shot "${name}": ${Object.keys(SHOTS).join(", ")}`);
  await SHOTS[name]();
}
await browser.close();
rmSync(work, { recursive: true, force: true });
