import { expect, test } from "@playwright/test";
import { PEOPLE, REELS } from "./env";

/* API tokens (#103): made on Your account, used from a script with no
   session, held to their scopes, and seen and revoked by an admin. */

test.describe("API tokens", () => {
  test("Bob makes one, a script reads his library with it, and Alice revokes it", async ({
    browser,
    playwright,
  }) => {
    const bob = await browser.newPage({ storageState: PEOPLE.bob.state });
    await bob.goto("/account");
    await expect(bob.getByRole("heading", { name: "API tokens" })).toBeVisible();
    // Not an admin: the admin scopes are not his to give; library and ai are.
    await expect(bob.locator(".t-scopes input")).toHaveCount(4);
    await expect(bob.getByText("No tokens.")).toBeVisible();

    await bob.getByLabel("Name", { exact: true }).fill("e2e monitor");
    await bob.getByLabel("Lasts").selectOption("7");
    await bob.getByRole("button", { name: "Make a token" }).click();
    const token = await bob.locator("#t-fresh-token").inputValue();
    expect(token).toMatch(/^hk_api_[0-9A-Za-z]{36}$/);
    const origin = new URL(bob.url()).origin;
    await expect(bob.locator("#t-fresh-example")).toHaveValue(
      `curl -H "Authorization: Bearer ${token}" ${origin}/api/stats`,
    );
    const row = bob.locator(".a-invite", { hasText: "e2e monitor" });
    await expect(row.getByText(/read:library · until .* · not used yet/)).toBeVisible();

    // A script, with no session: Bob's reels and nobody else's, and no writing.
    const script = await playwright.request.newContext({
      baseURL: origin,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
    const items = await script.get("/api/items");
    expect(items.status()).toBe(200);
    const codes = (await items.json()).items.map((i: { shortcode: string }) => i.shortcode).sort();
    expect(codes).toEqual([...REELS.bob, REELS.shared].sort());
    expect((await script.get(`/api/items/${REELS.alice[0]}`)).status()).toBe(404);
    const write = await script.put(`/api/items/${REELS.shared}/tag`, { data: { name: "Food" } });
    expect(write.status()).toBe(403);
    expect(write.headers()["www-authenticate"]).toContain('error="insufficient_scope"');
    expect((await script.get("/api/users")).status()).toBe(403);

    // Shown once: gone after a reload, and the list says it was used.
    await bob.reload();
    await expect(bob.locator("#t-fresh-token")).toHaveCount(0);
    await expect(row.getByText(/last used/)).toBeVisible();

    // Alice sees it on the Pipeline page, never its value, and revokes it.
    const alice = await browser.newPage({ storageState: PEOPLE.alice.state });
    await alice.goto("/pipeline");
    const listed = alice.locator(".p-token", { hasText: "e2e monitor" });
    await expect(listed.getByText(/^bob · read:library/)).toBeVisible();
    await expect(alice.getByText(token)).toHaveCount(0);
    await listed.getByRole("button", { name: "Revoke" }).click();
    await expect(alice.getByText("bob's token e2e monitor is revoked")).toBeVisible();
    await expect(listed).toHaveCount(0);

    const refused = await script.get("/api/items");
    expect(refused.status()).toBe(401);
    expect(refused.headers()["www-authenticate"]).toContain('error="invalid_token"');
    await script.dispose();
    await alice.close();
    await bob.close();
  });

  test("an admin may give an admin scope", async ({ browser }) => {
    const alice = await browser.newPage({ storageState: PEOPLE.alice.state });
    await alice.goto("/account");
    await expect(alice.locator(".t-scopes input")).toHaveCount(6);
    await expect(alice.locator(".t-scopes")).toContainText("write:admin");
    await alice.close();
  });
});
