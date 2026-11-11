import { expect, test, type Page } from "@playwright/test";
import { PEOPLE } from "./env";

/* The Pipeline page (#72): what each stage has to do, and whether a worker is
   there to do it. The seeded server runs no worker, which is the state the
   page has to make plain. */

test.describe("as Alice, an admin", () => {
  test.use({ storageState: PEOPLE.alice.state });

  test("the sidebar's pipeline leads to it", async ({ page }) => {
    await page.goto("/");
    const link = page.locator("a.pipeline-link");
    await expect(link).toHaveAttribute("href", "/pipeline");
    await expect(link.getByText("No worker running")).toBeAttached();
  });

  test("it says no worker is running, and what each stage has", async ({ page }) => {
    await page.goto("/pipeline");
    await expect(page.getByRole("heading", { name: "Pipeline", level: 1 })).toBeVisible();
    await expect(page.getByText("No worker has checked in")).toBeVisible();
    const rows = page.locator("tbody tr");
    await expect(rows).toHaveCount(6);
    await expect(rows.first().locator("td").first()).toHaveText("Fetch");
    await expect(rows.last().locator("td").first()).toHaveText("Embed");
  });

  test("it lists the reels that couldn't be downloaded, as the sidebar counts them (#113)", async ({ page }) => {
    await page.goto("/pipeline");
    const block = page.locator(".p-failed", { hasText: "couldn't be downloaded" });
    await block.getByText("1 reel couldn't be downloaded").click();
    const row = block.locator("li", { hasText: "ALICEfail001" });
    await expect(row.getByText("Video unavailable")).toBeVisible();
    await expect(row.getByText(/will be tried again/)).toBeVisible();
    // Still has tries left: nothing for Retry to offer yet.
    await expect(block.getByRole("button", { name: "Retry" })).toHaveCount(0);
  });

  test("the worker's log is a link away", async ({ page }) => {
    await page.goto("/pipeline");
    await page.getByRole("link", { name: "Worker log" }).click();
    await expect(page).toHaveURL(/\/logs\?log=worker$/);
    await expect(page.locator(".l-bar select").first()).toHaveValue("worker");
  });
});

test.describe("worker tokens, as Alice (#35)", () => {
  test.use({ storageState: PEOPLE.alice.state });

  test("she makes one, a worker uses it, and she revokes it", async ({ page, playwright }) => {
    await page.goto("/pipeline");
    await expect(page.getByText("No tokens, so no worker")).toBeVisible();
    await page.getByLabel("What the token is for").fill("e2e desktop");
    await page.getByRole("button", { name: "Make a token" }).click();

    const token = await page.locator("#p-fresh-token").inputValue();
    expect(token.length).toBeGreaterThan(30);
    const origin = new URL(page.url()).origin;
    await expect(page.locator("#p-fresh-command")).toHaveValue(
      `HIKIDASHI_SERVER=${origin} HIKIDASHI_WORKER_TOKEN=${token} uv run hikidashi worker`,
    );
    const row = page.locator(".p-token", { hasText: "e2e desktop" });
    await expect(row.getByText("not used yet")).toBeVisible();

    // A worker, with no session: the token alone lets it claim.
    const worker = await playwright.request.newContext({
      baseURL: origin,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
    const claim = () =>
      worker.post("/api/worker/claim", {
        data: { worker: "e2e", protocol: 1, versions: { asr: 1 }, stage: "asr", limit: 1 },
      });
    expect((await claim()).status()).not.toBe(401);

    // Shown once: gone after a reload, and the list says it was used.
    await page.reload();
    await expect(page.locator("#p-fresh-token")).toHaveCount(0);
    await expect(row.getByText(/last used/)).toBeVisible();

    await row.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByText("e2e desktop's token is revoked")).toBeVisible();
    await expect(row).toHaveCount(0);
    expect((await claim()).status()).toBe(404); // the last one gone: remote workers off
    await worker.dispose();
  });
});

test.describe("copying a new token (#107)", () => {
  test.use({ storageState: PEOPLE.alice.state });

  /* The tests' server is http://127.0.0.1, a secure context. A NAS opened
     over plain HTTP on the LAN isn't one: there the page has no
     navigator.clipboard, which these stub away to stand in for it. */
  const plainHttp = () => {
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    Object.defineProperty(Navigator.prototype, "clipboard", { get: () => undefined, configurable: true });
  };

  async function makeToken(page: Page, name: string) {
    await page.goto("/pipeline");
    await page.getByLabel("What the token is for").fill(name);
    await page.getByRole("button", { name: "Make a token" }).click();
    return page.locator("#p-fresh-token").inputValue();
  }

  async function revoke(page: Page, name: string) {
    await page.locator(".p-token", { hasText: name }).getByRole("button", { name: "Revoke" }).click();
    await expect(page.locator(".p-token", { hasText: name })).toHaveCount(0);
  }

  const copyButton = (page: Page, field: string) =>
    page.locator(".p-row", { has: page.locator(field) }).getByRole("button");

  test("over HTTPS, Copy puts the token on the clipboard", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const token = await makeToken(page, "e2e copy secure");
    await copyButton(page, "#p-fresh-token").click();
    await expect(copyButton(page, "#p-fresh-token")).toHaveText("Copied");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(token);
    await revoke(page, "e2e copy secure");
  });

  test("over plain HTTP, Copy still copies the command", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.addInitScript(plainHttp);
    await makeToken(page, "e2e copy http");
    expect(await page.evaluate(() => typeof navigator.clipboard)).toBe("undefined");
    const command = await page.locator("#p-fresh-command").inputValue();
    await copyButton(page, "#p-fresh-command").click();
    await expect(copyButton(page, "#p-fresh-command")).toHaveText("Copied");
    await expect(page.getByRole("alert")).toHaveCount(0);

    // Read it back from a page that still has the clipboard API.
    const reader = await context.newPage();
    await reader.goto("/pipeline");
    expect(await reader.evaluate(() => navigator.clipboard.readText())).toBe(command);
    await reader.close();
    await revoke(page, "e2e copy http");
  });

  test("when nothing can copy, it says so and selects the token", async ({ page }) => {
    await page.addInitScript(plainHttp);
    await page.addInitScript(() => { document.execCommand = () => false; });
    const token = await makeToken(page, "e2e copy none");
    await copyButton(page, "#p-fresh-token").click();
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("the text is selected");
    await expect(alert).toContainText("HTTPS");
    await expect(copyButton(page, "#p-fresh-token")).toHaveText("Copy");
    const selected = await page.locator("#p-fresh-token").evaluate(
      (el: HTMLInputElement) => el.value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0),
    );
    expect(selected).toBe(token);
    await revoke(page, "e2e copy none");
  });
});

test.describe("as Bob, who is not", () => {
  test.use({ storageState: PEOPLE.bob.state });

  test("it is not his to see", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("a.pipeline-link")).toHaveCount(0);
    await page.goto("/pipeline");
    await expect(page.getByText(/Could not read the pipeline/)).toBeVisible();
  });
});
