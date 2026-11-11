import { expect, test, type Page } from "@playwright/test";
import { PEOPLE, REELS } from "./env";

/* Exports (#56), from the three places the app offers them: the form on Your
   account, the Export menu over the grid, and a reel's sheet. Each is a link
   the browser saves; what it saves is checked by downloading it. */

async function download(page: Page, click: () => Promise<void>) {
  const [file] = await Promise.all([page.waitForEvent("download"), click()]);
  const path = await file.path();
  const { readFile } = await import("node:fs/promises");
  return { name: file.suggestedFilename(), text: await readFile(path, "utf-8") };
}

test.describe("as Alice, an admin", () => {
  test.use({ storageState: PEOPLE.alice.state });

  test("the account form exports a collection as CSV", async ({ page }) => {
    await page.goto("/account");
    await page.getByText("CSV", { exact: true }).click();
    await page.getByLabel("What").selectOption("c:Japan trip");
    await expect(page.getByText("2 reels", { exact: true })).toBeVisible();
    const got = await download(page, () => page.getByRole("link", { name: "Download" }).click());
    expect(got.name).toBe("hikidashi-alice-japan-trip-library.csv");
    const codes = got.text.split("\n").slice(1).filter(Boolean).map((l) => l.split(",")[0]);
    expect(codes.sort()).toEqual(["ALICEkyoto01", "ALICEtokyo01"]);
  });

  test("she can export Bob's library", async ({ page }) => {
    await page.goto("/account");
    await page.getByLabel("Whose library").selectOption("bob");
    await page.getByText("JSON", { exact: true }).click();
    const got = await download(page, () => page.getByRole("link", { name: "Download" }).click());
    expect(got.name).toBe("hikidashi-bob-library.json");
    const shown = JSON.parse(got.text).map((r: { shortcode: string }) => r.shortcode);
    expect(shown.sort()).toEqual([...REELS.bob, REELS.shared].sort());
  });

  test("the Export menu exports what the grid shows", async ({ page }) => {
    await page.goto("/?collection=Japan%20trip");
    await page.getByRole("button", { name: "Export", exact: true }).click();
    const got = await download(page, () => page.getByRole("link", { name: "JSON", exact: true }).click());
    const shown = JSON.parse(got.text).map((r: { shortcode: string }) => r.shortcode);
    expect(shown.sort()).toEqual(["ALICEkyoto01", "ALICEtokyo01"]);
  });

  test("a reel's sheet exports its recipe and its note", async ({ page }) => {
    await page.goto(`/item/${REELS.shared}`);
    const menu = page.locator(".sheet-actions");
    await menu.getByRole("button", { name: "Export", exact: true }).click();
    const recipe = await download(page, () => menu.getByRole("link", { name: "schema.org JSON-LD" }).click());
    expect(recipe.name).toBe(`hikidashi-alice-${REELS.shared}-recipe.json`);
    expect(JSON.parse(recipe.text)).toMatchObject({ "@type": "Recipe", name: "Carbonara" });

    await menu.getByRole("button", { name: "Export", exact: true }).click();
    const note = await download(page, () => menu.getByRole("link", { name: "Markdown note" }).click());
    expect(note.text).toContain("# Carbonara");
    // Escape closes the menu and leaves the sheet open.
    await menu.getByRole("button", { name: "Export", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(menu.getByRole("link", { name: "Markdown note" })).toHaveCount(0);
    await expect(page.locator("dialog.sheet[open]")).toHaveCount(1);
  });
});

test.describe("as Bob", () => {
  test.use({ storageState: PEOPLE.bob.state });

  test("he is not offered anyone else's library", async ({ page }) => {
    await page.goto("/account");
    await expect(page.getByRole("link", { name: "Download" })).toBeVisible();
    await expect(page.getByLabel("Whose library")).toHaveCount(0);
  });

  test("a reel with no recipe offers only the reel", async ({ page }) => {
    await page.goto(`/item/${REELS.bob[0]}`);
    const menu = page.locator(".sheet-actions");
    await menu.getByRole("button", { name: "Export", exact: true }).click();
    await expect(menu.getByText("This reel")).toBeVisible();
    await expect(menu.getByText("This recipe")).toHaveCount(0);
  });
});
