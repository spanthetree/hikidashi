/* What the demo scripts share (record-demo.mjs, screenshots.mjs): where the
   demo library's server is, how to sign in to it, and the fonts a Windows
   browser draws the default theme in. */

import { existsSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "@playwright/test";

export const BASE = "http://127.0.0.1:8090";
export const USER = process.env.DEMO_USER ?? "spanthetree";
export const DEMO = process.env.DEMO_ROOT ?? join(homedir(), ".cache/hikidashi-demo");
export const FONTS = process.env.DEMO_FONTS ?? "/mnt/c/Windows/Fonts";
export const FACES = [
  ...["segoeui", "segoeuib", "segoeuii", "segoeuiz", "segoeuil", "segoeuisl", "seguisb", "seguibl"],
  ...["seguiemj", "consola", "consolab", "YuGothR", "YuGothM", "YuGothB"],
];

/* A fontconfig of its own for the filmed browser: the faces above first for
   sans-serif, system-ui and monospace, then the machine's own. The aliases
   come before the include, so they are preferred over the system's. */
export function fontconfig(dir) {
  const files = FACES.map((f) => [".ttf", ".ttc"].map((x) => join(FONTS, f + x)).find(existsSync));
  if (files.includes(undefined)) {
    console.warn(`  fonts missing from ${FONTS}: filming in this machine's own fonts`);
    return {};
  }
  mkdirSync(join(dir, "fonts"));
  for (const f of files) symlinkSync(f, join(dir, "fonts", f.split("/").pop()));
  const prefer = (generic, ...families) =>
    `<alias binding="strong"><family>${generic}</family><prefer>` +
    families.map((f) => `<family>${f}</family>`).join("") +
    "</prefer></alias>";
  const sans = ["Segoe UI", "Yu Gothic UI", "Segoe UI Emoji"];
  writeFileSync(
    join(dir, "fonts.conf"),
    `<?xml version="1.0"?>\n<fontconfig>\n<dir>${join(dir, "fonts")}</dir>\n` +
      `<cachedir>${join(dir, "cache")}</cachedir>\n` +
      [prefer("sans-serif", ...sans), prefer("system-ui", ...sans), prefer("emoji", "Segoe UI Emoji"),
        prefer("monospace", "Consolas", "Yu Gothic UI")].join("\n") +
      `\n<include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n</fontconfig>\n`,
  );
  return { FONTCONFIG_FILE: join(dir, "fonts.conf") };
}

export async function session() {
  /* Sign in off camera, and hand the cookie to the browser that is filmed. */
  const browser = await chromium.launch();
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(BASE + "/login");
  await page.getByLabel("Username").fill(USER);
  await page.getByLabel("Password").fill(readFileSync(join(DEMO, "password"), "utf8").trim());
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(BASE + "/");
  // Each take starts from the same library: an empty shopping list.
  await page.evaluate(() => fetch("/api/shopping", { method: "DELETE" }));
  const state = await ctx.storageState();
  await browser.close();
  return state;
}
