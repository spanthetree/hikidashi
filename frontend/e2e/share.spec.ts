import { expect, test } from "@playwright/test";
import { PEOPLE } from "./env";

/* #52: installable, and the phone's share sheet adds a reel. The share target
   is a GET to /share with the shared text, the same as a person following it. */

const shared = (code: string) =>
  `/share?title=Reel&text=${encodeURIComponent(`Look at this https://www.instagram.com/reel/${code}/?igsh=x`)}`;

test.describe("share to Hikidashi", () => {
  test("a shared reel lands in the library, once", async ({ browser }) => {
    const bob = await browser.newPage({ storageState: PEOPLE.bob.state });
    await bob.goto(shared("SHARE1"));
    await expect(bob.getByRole("status")).toHaveText("Added. Fetching it now…");
    await bob.getByRole("link", { name: "Open it" }).click();
    await expect(bob).toHaveURL(/\/item\/SHARE1$/);

    await bob.goto(shared("SHARE1"));
    await expect(bob.getByRole("status")).toHaveText("That one is already in your library.");
    await bob.goto("/share?text=hello");
    await expect(bob.getByRole("status")).toContainText("not a link to an Instagram post");
  });

  test("signed out, it signs in first and keeps the share", async ({ page }) => {
    await page.goto(shared("SHARE2"));
    await expect(page).toHaveURL(/\/login\?next=%2Fshare/);
    await page.getByLabel("Username").fill(PEOPLE.alice.username);
    await page.getByLabel("Password").fill(PEOPLE.alice.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("status")).toHaveText("Added. Fetching it now…");
  });

  test("Add by link in the Import dialog", async ({ browser }) => {
    const bob = await browser.newPage({ storageState: PEOPLE.bob.state });
    await bob.goto("/");
    await bob.getByRole("button", { name: "Import" }).click();
    await bob.getByLabel("Or add one reel by its link").fill("https://www.instagram.com/p/SHARE3/");
    await bob.getByRole("button", { name: "Add", exact: true }).click();
    await expect(bob.getByRole("dialog").getByRole("status")).toContainText("Added.");
  });

  test("installable: a manifest with a share target, and a service worker", async ({ page }) => {
    const manifest = await (await page.request.get("/manifest.webmanifest")).json();
    expect(manifest.share_target).toMatchObject({ action: "/share", method: "GET" });
    expect(manifest.icons.some((i: { sizes: string }) => i.sizes === "512x512")).toBe(true);

    await page.goto("/login");
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", /manifest\.webmanifest$/);
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    expect(new URL(scope).pathname).toBe("/");
    // The API is never cached: only the shell is.
    const cached = await page.evaluate(async () => {
      const keys = await caches.keys();
      const urls = (await Promise.all(keys.map(async (k) => (await caches.open(k)).keys()))).flat();
      return urls.map((r) => new URL(r.url).pathname);
    });
    expect(cached).toContain("/");
    expect(cached.filter((p) => p.startsWith("/api/") || p.startsWith("/media/"))).toEqual([]);
  });
});
