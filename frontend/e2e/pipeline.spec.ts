import { expect, test } from "@playwright/test";
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

test.describe("as Bob, who is not", () => {
  test.use({ storageState: PEOPLE.bob.state });

  test("it is not his to see", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("a.pipeline-link")).toHaveCount(0);
    await page.goto("/pipeline");
    await expect(page.getByText(/Could not read the pipeline/)).toBeVisible();
  });
});
