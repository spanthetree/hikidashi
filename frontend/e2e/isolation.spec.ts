import { expect, test } from "@playwright/test";
import { PEOPLE, REELS } from "./env";

/* Flow 2: one person's library is invisible to another (#12), in the browser
   and not only at the API. Bob shares one reel with Alice and has two of his
   own; the rest of hers he must never see, down to whether they exist. */

const cards = (page: import("@playwright/test").Page) => page.locator("[data-card]");

test.describe("as Bob", () => {
  test.use({ storageState: PEOPLE.bob.state });

  test("the grid holds his reels and none of Alice's", async ({ page }) => {
    await page.goto("/");
    await expect(cards(page)).toHaveCount(REELS.bob.length + 1);
    const shown = await cards(page).evaluateAll((els) => els.map((e) => e.dataset.card));
    expect(shown.sort()).toEqual([...REELS.bob, REELS.shared].sort());
  });

  test("the sidebar counts only his library", async ({ page }) => {
    await page.goto("/");
    // Counted, not looked at: the sidebar starts as a rail of icons, its
    // labels hidden until it opens. Alice's collection and her tags are not
    // even names in it.
    const sidebar = page.locator("#sidebar");
    await expect(sidebar.getByText("Finance", { exact: true })).toHaveCount(1);
    await expect(sidebar.getByText("Dinners", { exact: true })).toHaveCount(1);
    await expect(sidebar.getByText("Japan trip")).toHaveCount(0);
    await expect(sidebar.getByText("Automotive")).toHaveCount(0);
  });

  test("searching does not find Alice's reels", async ({ page }) => {
    await page.goto("/?q=tokyo");
    await expect(page.getByText("No reels match that filter")).toBeVisible();
    await expect(cards(page)).toHaveCount(0);
  });

  test("a link to Alice's reel opens nothing", async ({ page }) => {
    await page.goto(`/item/${REELS.alice[1]}`);
    await expect(page.getByText("Could not open that reel")).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page).toHaveURL("/");
  });

  test("Alice's reel and its files are a 404, like a reel nobody has", async ({ page }) => {
    for (const code of [REELS.alice[1], "NOSUCHreel01"]) {
      const res = await page.request.get(`/api/items/${code}`);
      expect(res.status()).toBe(404);
    }
    const thumb = `/media/thumbs/al/${REELS.alice[1]}.png`;
    expect((await page.request.get(thumb)).status()).toBe(404);
  });

  test("the reel they share opens", async ({ page }) => {
    await page.goto(`/item/${REELS.shared}`);
    await expect(page.getByRole("dialog")).toContainText("Weeknight carbonara");
  });
});

test.describe("as Alice", () => {
  test.use({ storageState: PEOPLE.alice.state });

  test("her own reel opens, and its thumbnail loads", async ({ page }) => {
    await page.goto(`/item/${REELS.alice[1]}`);
    await expect(page.getByRole("dialog")).toContainText("A morning in Tokyo");
    const thumb = `/media/thumbs/al/${REELS.alice[1]}.png`;
    expect((await page.request.get(thumb)).status()).toBe(200);
  });

  test("the grid never shows Bob's own reels", async ({ page }) => {
    await page.goto("/");
    await expect(cards(page).first()).toBeVisible();
    for (const code of REELS.bob) {
      await expect(page.locator(`[data-card="${code}"]`)).toHaveCount(0);
    }
    await expect(page.locator(`[data-card="${REELS.shared}"]`)).toHaveCount(1);
  });
});
