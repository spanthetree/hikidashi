import { expect, test as setup } from "@playwright/test";
import { PEOPLE } from "./env";

/* Sign each person in through the page once, and keep the cookie for the
   tests that run as them. */
for (const who of Object.values(PEOPLE)) {
  setup(`sign in as ${who.username}`, async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Username").fill(who.username);
    await page.getByLabel("Password").fill(who.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL("/");
    await expect(page.locator("[data-card]").first()).toBeVisible();
    await page.context().storageState({ path: who.state });
  });
}
