/* Record the app on the demo library: the README loop and the feature tour.

     uv run python scripts/demo_library.py serve     # in the repository, left running
     node scripts/record-demo.mjs hero               # here: ../docs/demo/app.webp
     node scripts/record-demo.mjs tour               # ../docs/demo/tour.mp4

   A real Chromium in a virtual display (Xvfb), driven by Playwright, and
   ffmpeg grabbing that display at 30 fps. Playwright's own video runs at
   about 25 fps and has no pointer, so the page draws one: a cursor that
   follows the mouse events sent here, with a ripple on each click. Moves are
   eased along a slight arc and typing is paced, so it reads at normal speed.

   Filmed in dark mode, in the fonts a Windows browser draws the default
   theme in (Segoe UI, Yu Gothic UI, Segoe UI Emoji, Consolas), taken from
   DEMO_FONTS (the Windows font folder as WSL sees it, by default) for this
   browser alone. Without them Chromium falls back to DejaVu Sans, which is
   not what anyone sees the app in.

   Needs Xvfb, ffmpeg (with libwebp and libx264) and Playwright's Chromium
   (`npx playwright install chromium`). Run it again when the UI changes; it
   is not checked in CI, as no two recordings are the same. */

import { spawn } from "node:child_process";
import { copyFileSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { BASE, fontconfig, session } from "./demo-common.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const OUT = join(ROOT, "docs/demo");
const [W, H] = [1280, 800];
const FPS = 30;
const DISPLAY = ":97";

// Where the pointer rests between moves: empty space beside the count, where
// no tooltip opens. The loop starts and ends here.
const REST = [W * 0.42, 88];

const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));
const ease = (t) => t * t * (3 - 2 * t);

const CURSOR = `(() => {
  if (window.__rvCursor) return;
  window.__rvCursor = true;
  const css = \`
    #hk-cursor { position: fixed; left: 0; top: 0; width: 24px; height: 24px; z-index: 2147483647;
                 pointer-events: none; will-change: transform; }
    .hk-ripple { position: fixed; z-index: 2147483646; pointer-events: none; width: 38px; height: 38px;
                 margin: -19px 0 0 -19px; border-radius: 50%; border: 2px solid rgba(37,99,235,.9);
                 background: rgba(37,99,235,.18); animation: hk-ripple .45s ease-out forwards; }
    @keyframes hk-ripple { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.4); opacity: 0 } }\`;
  const add = () => {
    const style = document.createElement('style'); style.textContent = css;
    document.head.appendChild(style);
    const c = document.createElement('div'); c.id = 'hk-cursor';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M4 2.5 L4 19 L8.6 14.6 ' +
      'L11.8 21.6 L14.6 20.4 L11.5 13.5 L17.8 13.3 Z" fill="#111" stroke="#fff" stroke-width="1.5" ' +
      'stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    const at = JSON.parse(sessionStorage.getItem('hk-cursor') || '[-100,-100]');
    const put = (x, y) => { c.style.transform = 'translate(' + (x - 4) + 'px,' + (y - 2.5) + 'px)'; };
    put(at[0], at[1]);
    addEventListener('mousemove', e => {
      put(e.clientX, e.clientY);
      sessionStorage.setItem('hk-cursor', JSON.stringify([e.clientX, e.clientY]));
    }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div'); r.className = 'hk-ripple';
      r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.documentElement.appendChild(r); setTimeout(() => r.remove(), 500);
    }, true);
  };
  if (document.head) add(); else addEventListener('DOMContentLoaded', add);
})();`;

/* The mouse and keyboard, moved the way a person moves them. */
class Director {
  constructor(page) {
    this.page = page;
    [this.x, this.y] = REST;
  }

  pause(s) {
    return sleep(s);
  }

  async move(x, y, seconds) {
    const dist = Math.hypot(x - this.x, y - this.y);
    seconds ??= Math.min(1.0, 0.35 + dist / 1500);
    const frames = Math.max(2, Math.round(seconds * FPS));
    const [x0, y0] = [this.x, this.y];
    // A slight arc, as a wrist moves, rather than a ruler-straight line.
    const bend = Math.min(50, dist * 0.1);
    const [nx, ny] = dist ? [-(y - y0) / dist, (x - x0) / dist] : [0, 0];
    const start = performance.now();
    for (let i = 1; i <= frames; i++) {
      const e = ease(i / frames);
      const arc = Math.sin(Math.PI * e) * bend;
      await this.page.mouse.move(x0 + (x - x0) * e + nx * arc, y0 + (y - y0) * e + ny * arc);
      const wait = start + (i * 1000) / FPS - performance.now();
      if (wait > 0) await sleep(wait / 1000);
    }
    [this.x, this.y] = [x, y];
  }

  async to(locator, { dx = 0.5, dy = 0.5, seconds } = {}) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) throw new Error(`not on screen: ${locator}`);
    await this.move(box.x + box.width * dx, box.y + box.height * dy, seconds);
  }

  /* Until nothing on the page is moving: results re-sorting, a sheet opening
     or closing. A click while they move lands where the target was, not
     where it is. Endless animations (a loading shimmer) do not count. */
  async still(max = 2.5) {
    const end = performance.now() + max * 1000;
    while (performance.now() < end) {
      const moving = await this.page.evaluate(
        () =>
          document
            .getAnimations()
            .filter((a) => a.playState === "running" && a.effect?.getComputedTiming().iterations !== Infinity)
            .length,
      );
      if (!moving) return;
      await sleep(0.05);
    }
  }

  async click(locator, { settle = 0.7, ...at } = {}) {
    await this.still();
    await this.to(locator, at);
    await this.still();
    await sleep(0.12);
    await this.page.mouse.down();
    await sleep(0.07);
    await this.page.mouse.up();
    await sleep(settle);
  }

  async type(text, cps = 9) {
    let i = 0;
    for (const ch of text) {
      await this.page.keyboard.type(ch);
      await sleep((1 / cps) * (0.7 + (0.6 * ((i++ * 7) % 5)) / 4));
    }
  }

  async clear() {
    await this.page.keyboard.press("Control+A");
    await sleep(0.15);
    await this.page.keyboard.press("Backspace");
    await sleep(0.4);
  }

  async scroll(dy, seconds = 1.2) {
    const frames = Math.max(1, Math.round(seconds * FPS));
    let done = 0;
    for (let i = 1; i <= frames; i++) {
      const want = dy * ease(i / frames);
      await this.page.mouse.wheel(0, want - done);
      done = want;
      await sleep(1 / FPS);
    }
  }
}

/* Each scene starts on the library, signed in, and is filmed from its first
   frame to its last. */

const search = (d) => d.page.getByLabel("Search");
const card = (d, code) => d.page.locator(`[data-card="${code}"]`);

/* The sidebar opens while the pointer is over the rail. */
async function side(d, name, { exact = true } = {}) {
  await d.move(32, 200);
  await sleep(0.6);
  await d.click(d.page.locator("#sidebar").getByText(name, { exact }).first(), { settle: 1.2 });
  await d.move(W * 0.62, H * 0.5);
}

async function find(d, query, settle = 1.6) {
  await d.click(search(d), { dx: 0.25, settle: 0.3 });
  await d.type(query);
  // Japanese is confirmed with Enter, as an input method asks; until then the
  // text is still being composed, and a click elsewhere goes to finishing it.
  if (/[^\x00-\x7f]/.test(query)) await d.page.keyboard.press("Enter");
  await sleep(settle);
}

async function clear(d) {
  await d.click(search(d), { dx: 0.85, settle: 0.2 });
  await d.clear();
  await sleep(0.8);
}

async function open(d, code, { hover = 1.2, settle = 2.2 } = {}) {
  const c = card(d, code).locator("a.thumb");
  await d.to(c);
  await sleep(hover); // the preview plays in the card
  const sheet = d.page.locator("dialog.sheet[open]");
  await d.click(c, { settle: 0.2 });
  // A click that lands as the results reflow can miss; click again, as a
  // person would.
  if (!(await sheet.waitFor({ timeout: 4000 }).then(() => true, () => false))) {
    await d.click(c, { settle: 0.2 });
    await sheet.waitFor();
  }
  await sleep(settle);
}

async function close(d) {
  await d.click(d.page.locator(".sheet-close"), { settle: 1 });
}

const SCENES = {
  /* The README loop, about 40 s: a search by meaning and the recipe in that
     reel, the recipes from the sidebar, the places on the map, and back to
     where it started. */
  async hero(d) {
    await sleep(1.2);
    await find(d, "something with coconut");
    await open(d, "DQcurry0001", { settle: 1.2 });
    await d.click(d.page.getByRole("tab", { name: "Recipe" }), { settle: 1.2 });
    await d.to(d.page.locator(".sheet-body"), { dy: 0.6, seconds: 0.6 });
    await d.scroll(260, 1.2);
    await sleep(1.4);
    await close(d);
    await clear(d);

    await side(d, "Recipes");
    await sleep(1.4);
    await side(d, "30 min or less");
    await sleep(1.6);

    await side(d, "Places");
    await d.click(d.page.getByRole("button", { name: /^Map/ }).first(), { settle: 2.5 });
    await d.click(d.page.locator(".hk-cluster").filter({ hasText: "3" }), { settle: 2.2 });
    await d.click(d.page.locator(".hk-pin").first(), { settle: 2.2 });

    await side(d, "All saves");
    await d.move(...REST);
    await sleep(1.2);
  },

  /* The tour, about 80 s: search three ways, a carousel, recipes into a
     shopping list, the map, a collection, and the workers. */
  async tour(d) {
    await sleep(1);
    await d.move(W * 0.55, H * 0.6);
    await d.scroll(900, 2.2);
    await sleep(0.8);
    await d.scroll(-900, 1.6);

    await find(d, "something sweet to bake", 2.2);
    await clear(d);
    await find(d, "a sandwich for lunch", 2.2);
    await clear(d);

    await find(d, "something with coconut");
    await open(d, "DQcurry0001", { settle: 1.2 });
    await d.click(d.page.getByRole("tab", { name: "Recipe" }), { settle: 1 });
    await d.to(d.page.locator(".sheet-body"), { dy: 0.6, seconds: 0.6 });
    await d.scroll(300, 1.4);
    await sleep(1.2);
    await close(d);
    await clear(d);

    await find(d, "ラーメン", 2);
    await open(d, "DQshioramen", { hover: 0.6 });
    await d.click(d.page.locator(".slide-step.next"), { settle: 1.4 });
    await close(d);
    await clear(d);

    await find(d, "a cat", 2);
    await clear(d);

    await side(d, "Recipes");
    await sleep(1);
    await side(d, "30 min or less");
    await open(d, "DQpRoastAp1", { hover: 0.5 });
    await d.click(d.page.getByRole("button", { name: "Add to shopping list" }), { settle: 1.2 });
    await close(d);
    await side(d, "Shopping list");
    await sleep(2.5);
    await d.click(d.page.getByRole("link", { name: /Recipes/ }).first(), { settle: 1.2 });

    await side(d, "Places");
    await d.click(d.page.getByRole("button", { name: /^Map/ }).first(), { settle: 2.5 });
    await d.click(d.page.locator(".leaflet-control-zoom-in"), { settle: 0.8 });
    await d.move(W * 0.7, H * 0.55);
    await sleep(1.5);

    await side(d, "Japan trip");
    await sleep(2);

    await d.move(32, 200);
    await sleep(0.6);
    await d.click(d.page.locator("a.pipeline-link").first(), { settle: 3 });
    await d.scroll(400, 1.2);
    await sleep(2);
    await d.click(d.page.getByRole("link", { name: /Library/ }).first(), { settle: 1.2 });

    await d.move(W * 0.5, H * 0.6);
    await d.scroll(500, 1.6);
    await sleep(2);
  },
};

function run(cmd, args, opts = {}) {
  return new Promise((ok, fail) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "inherit", "inherit"], ...opts });
    p.on("exit", (code) => (code === 0 ? ok() : fail(new Error(`${cmd} exited ${code}`))));
  });
}


async function record(scene, raw, { theme = "dark", start = "/" } = {}) {
  const state = await session();
  const profile = mkdtempSync(join(tmpdir(), "hk-profile-"));
  const fontDir = mkdtempSync(join(tmpdir(), "hk-fonts-"));
  const fonts = fontconfig(fontDir);
  const xvfb = spawn("Xvfb", [DISPLAY, "-screen", "0", `${W}x${H}x24`, "-nolisten", "tcp"], {
    stdio: "ignore",
  });
  let grab;
  try {
    await sleep(1);
    // A persistent context runs in the browser's own window, which --kiosk
    // makes full screen with no tabs or address bar; a new context would not.
    const ctx = await chromium.launchPersistentContext(profile, {
      headless: false,
      env: { ...process.env, ...fonts, DISPLAY },
      viewport: null,
      colorScheme: theme,
      locale: "en-GB",
      timezoneId: "Asia/Tokyo",
      args: [
        "--kiosk",
        `--window-size=${W},${H}`,
        "--window-position=0,0",
        "--hide-scrollbars",
        "--autoplay-policy=no-user-gesture-required",
        "--force-color-profile=srgb",
        "--disable-features=Translate",
      ],
    });
    await ctx.addCookies(state.cookies);
    await ctx.addInitScript(CURSOR);
    const page = ctx.pages()[0] ?? (await ctx.newPage());
    await page.goto(BASE + start);
    await page.waitForLoadState("networkidle");
    await page.mouse.move(...REST);
    await sleep(1.5); // thumbnails in, fonts settled
    grab = spawn(
      "ffmpeg",
      [
        ...["-hide_banner", "-loglevel", "error", "-y", "-f", "x11grab"],
        ...["-framerate", String(FPS), "-draw_mouse", "0", "-video_size", `${W}x${H}`],
        ...["-i", DISPLAY, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10"],
        ...["-pix_fmt", "yuv444p", raw],
      ],
      { stdio: ["pipe", "inherit", "inherit"] },
    );
    await sleep(0.5);
    try {
      await scene(new Director(page));
    } catch (err) {
      // What was on screen when it went wrong, to see why.
      await page.screenshot({ path: join(tmpdir(), "hk-record-failed.png") });
      console.error(`  failed at ${page.url()}; screen in ${join(tmpdir(), "hk-record-failed.png")}`);
      const under = await page.evaluate(() => {
        const [x, y] = JSON.parse(sessionStorage.getItem("hk-cursor") ?? "[0,0]");
        const els = document.elementsFromPoint(x, y).slice(0, 4);
        const cards = [...document.querySelectorAll("[data-card]")].slice(0, 3).map((c) => {
          const r = c.getBoundingClientRect();
          return `${c.dataset.card}@${Math.round(r.x)},${Math.round(r.y)} ${getComputedStyle(c).transform} ${c.getAnimations({ subtree: true }).length}`;
        });
        return `${x},${y}: ${els.map((e) => `${e.tagName}.${String(e.className).split(" ")[0]}`).join(" < ")}; ${cards.join("; ")}`;
      });
      console.error(`  under the pointer at ${under}`);
      throw err;
    }
    grab.stdin.write("q");
    await new Promise((r) => grab.on("exit", r));
    grab = null;
    await ctx.close();
  } finally {
    if (grab) grab.kill("SIGINT");
    xvfb.kill();
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
      rmSync(fontDir, { recursive: true, force: true });
    } catch {} // Chromium may still be writing it; it is in the temp folder
  }
}

/* An animated WebP that loops forever, for the README. */
const webp = (raw, out, width = 1000, q = 70) =>
  run("ffmpeg", [
    ...["-hide_banner", "-loglevel", "error", "-y", "-i", raw],
    ...["-vf", `fps=${FPS},scale=${width}:-1:flags=lanczos`],
    ...["-c:v", "libwebp_anim", "-lossless", "0", "-q:v", String(q)],
    ...["-compression_level", "6", "-loop", "0", "-an", out],
  ]);

/* H.264 that plays anywhere, small enough to attach to a GitHub page. */
const mp4 = (raw, out) =>
  run("ffmpeg", [
    ...["-hide_banner", "-loglevel", "error", "-y", "-i", raw],
    ...["-c:v", "libx264", "-preset", "slow", "-crf", "28", "-pix_fmt", "yuv420p"],
    ...["-movflags", "+faststart", "-an", out],
  ]);

const [what, ...flags] = process.argv.slice(2);
if (!SCENES[what]) {
  console.error(`usage: node scripts/record-demo.mjs ${Object.keys(SCENES).join("|")} [--light] [--keep]`);
  process.exit(2);
}
const tmp = mkdtempSync(join(tmpdir(), "hk-record-"));
const raw = join(tmp, "raw.mkv");
try {
  await record(SCENES[what], raw, { theme: flags.includes("--light") ? "light" : "dark" });
  const out = join(OUT, what === "hero" ? "app.webp" : "tour.mp4");
  await (what === "hero" ? webp(raw, out) : mp4(raw, out));
  if (flags.includes("--keep")) copyFileSync(raw, out.replace(/\.\w+$/, ".raw.mkv"));
  console.log(`  wrote ${out.slice(ROOT.length + 1)} (${(statSync(out).size / 1e6).toFixed(1)} MB)`);
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
