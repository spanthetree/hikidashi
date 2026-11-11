import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { SETUP_TOKEN } from "./env";

/* Flow 1: a new library is claimed with the link the server logs, its owner
   signs out and back in, and invites someone who makes an account. One
   library, so the steps run in order. */
test.describe.configure({ mode: "serial" });

const OWNER = { username: "owner", password: "owner-password" };
const GUEST = { username: "guest", password: "guest-password" };

async function credentials(page: Page, who: { username: string; password: string },
                           button: string) {
  await page.getByLabel("Username").fill(who.username);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: button }).click();
}

test("signing in waits for the library to be claimed", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login\?next=%2F$/);
  await expect(page.getByText("No admin can sign in yet.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeDisabled();
});

test("a setup link with the wrong token is refused", async ({ page }) => {
  await page.goto("/setup?token=not-this-servers");
  await credentials(page, OWNER, "Claim it");
  await expect(page.getByRole("alert")).toContainText("not this server's");
});

test("the setup link claims the library", async ({ page }) => {
  const token = readFileSync(SETUP_TOKEN, "utf8").trim();
  await page.goto(`/setup?token=${encodeURIComponent(token)}`);
  await credentials(page, OWNER, "Claim it");
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "Nothing imported yet" })).toBeVisible();
  await expect(page.getByRole("button", { name: /owner/ })).toBeVisible();

  // A new library opens on the import box, starting from Instagram's export page.
  const link = page.getByRole("link", { name: /Open Instagram's export page/ });
  await expect(link).toHaveAttribute("href", "https://accountscenter.instagram.com/info_and_permissions/dyi/");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(page.getByRole("button", { name: /Drop the ZIP here, or click to choose it/ }))
    .toBeVisible();
});

test("the owner signs out and back in", async ({ page }) => {
  await page.goto("/login");
  await credentials(page, OWNER, "Sign in");
  await expect(page).toHaveURL("/");

  await page.goto("/account");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL("/login");
  // Signed out for real: the library sends you back here.
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);

  await credentials(page, { ...OWNER, password: "not-the-password" }, "Sign in");
  await expect(page.getByRole("alert")).toContainText("don't match");
  await credentials(page, OWNER, "Sign in");
  await expect(page).toHaveURL("/");
});

test("an invite link makes one account, once", async ({ page, browser }) => {
  await page.goto("/login");
  await credentials(page, OWNER, "Sign in");
  await expect(page).toHaveURL("/");
  await page.goto("/account");
  await page.getByRole("button", { name: "Make an invite link" }).click();
  const link = await page.locator("#a-fresh").inputValue();
  expect(link).toMatch(/\/invite\/[\w-]+$/);

  // The guest, in a browser of their own.
  const guest = await browser.newContext();
  const theirs = await guest.newPage();
  await theirs.goto(link);
  await credentials(theirs, GUEST, "Make account");
  await expect(theirs).toHaveURL("/");
  await expect(theirs.getByRole("heading", { name: "Nothing imported yet" })).toBeVisible();
  await theirs.goto("/account");
  await expect(theirs.getByText("Signed in as guest")).toBeVisible();
  // Not an admin: no invites, no accounts to manage.
  await expect(theirs.getByRole("button", { name: "Make an invite link" })).toHaveCount(0);
  await guest.close();

  // Used up.
  const late = await browser.newContext();
  const again = await late.newPage();
  await again.goto(link);
  await expect(again.getByText("This invite link has expired or has been used.")).toBeVisible();
  await expect(again.getByRole("button", { name: "Make account" })).toBeDisabled();
  await late.close();
});
