import { expect, test } from "@playwright/test";
import { PEOPLE } from "./env";

/* The API explorer (#147): signed in only, nothing from elsewhere, a token
   kept in the page, and a token's scope shown when it is not enough. */

test.describe("API explorer", () => {
  test("Bob explores the API with a read-only token", async ({ browser }) => {
    const bob = await browser.newPage({ storageState: PEOPLE.bob.state });
    const elsewhere: string[] = [];
    bob.on("request", (r) => {
      const here = bob.url().startsWith("http") ? new URL(bob.url()).origin : null;
      if (here && new URL(r.url()).origin !== here) elsewhere.push(r.url());
    });

    // A read-only token, made where tokens are made.
    await bob.goto("/account");
    await bob.getByLabel("Name", { exact: true }).fill("e2e explorer");
    await bob.getByRole("button", { name: "Make a token" }).click();
    const token = await bob.locator("#t-fresh-token").inputValue();

    await bob.getByRole("link", { name: /Explore the API/ }).click();
    await expect(bob).toHaveURL(/\/api\/docs$/);
    await expect(bob.getByText("Library", { exact: true }).first()).toBeVisible();
    // Not an admin: no admin or worker routes.
    await expect(bob.getByText("/api/users", { exact: true })).toHaveCount(0);
    await expect(bob.getByText("/api/worker/claim", { exact: true })).toHaveCount(0);

    // Authorize with the token, then run GET /api/items.
    await bob.getByRole("button", { name: "Authorize" }).first().click();
    const apiToken = bob.locator(".auth-container").first();
    await apiToken.locator("input").fill(token);
    await apiToken.getByRole("button", { name: "Apply credentials" }).click();
    await apiToken.getByRole("button", { name: "Close" }).click();
    const items = bob.locator("#operations-Library-items_api_items_get");
    await items.click();
    await items.getByRole("button", { name: "Try it out" }).click();
    await items.getByRole("button", { name: "Execute" }).click();
    await expect(items.locator(".live-responses-table tbody .response-col_status").first()).toHaveText("200");

    // The token is in the page, not saved.
    const kept = await bob.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
    expect(kept).not.toContain(token);
    // Writes need a tick, and then the token's scope shows.
    const tag = bob.locator("#operations-Library-set_tag_api_items__shortcode__tag_put");
    await tag.click();
    await expect(tag.getByText("Requires write:library")).toBeVisible();
    await expect(tag.getByRole("button", { name: "Try it out" })).toHaveCount(0);
    expect(elsewhere).toEqual([]);

    // Leave Bob with no tokens, as the other specs expect.
    const origin = new URL(bob.url()).origin;
    const mine = await (await bob.request.get(`${origin}/api/me/tokens`)).json();
    for (const t of mine.tokens) {
      const r = await bob.request.delete(`${origin}/api/me/tokens/${t.id}`, { headers: { Origin: origin } });
      expect(r.status()).toBe(200);
    }
  });

  test("signed out goes to sign in", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto("/api/docs");
    await expect(page).toHaveURL(/\/login\?next=/);
  });
});
