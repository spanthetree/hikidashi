import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { PEOPLE, REELS, UPLOAD } from "./env";

/* Flow 3: an export ZIP, chosen in the import box or dropped on it or on the
   page, lands in the grid, and only in the library of whoever imported it. */
test.describe.configure({ mode: "serial" });

const zip = () => ({ name: "upload.zip", mime: "application/zip", bytes: readFileSync(UPLOAD) });
const notZip = () => ({ name: "notes.txt", mime: "text/plain", bytes: Buffer.from("not an export") });

/** Drag files onto `target` and let go, as a desktop browser would. */
async function drop(page: Page, target: Locator, files: ReturnType<typeof zip>[]) {
  const dataTransfer = await page.evaluateHandle((fs) => {
    const dt = new DataTransfer();
    for (const f of fs) {
      const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
      dt.items.add(new File([bytes], f.name, { type: f.mime }));
    }
    return dt;
  }, files.map((f) => ({ name: f.name, mime: f.mime, b64: f.bytes.toString("base64") })));
  // Held as one element: the zone's name changes once a file is over it.
  const el = await target.elementHandle();
  for (const type of ["dragenter", "dragover", "drop"]) {
    await el!.dispatchEvent(type, { dataTransfer });
  }
}

async function openImport(page: Page) {
  await page.goto("/");
  await expect(page.locator("[data-card]").first()).toBeVisible();
  await page.getByRole("button", { name: "Import", exact: true }).click();
  return page.getByRole("dialog", { name: "Import your Instagram export" });
}

test.describe("as Alice", () => {
  test.use({ storageState: PEOPLE.alice.state });

  test("a ZIP chosen in the import box adds its reels", async ({ page }) => {
    const dialog = await openImport(page);
    await dialog.locator("input[type=file]").setInputFiles(UPLOAD);
    await expect(dialog.getByText(/Imported upload\.zip via .*: 2 new/)).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
    for (const code of REELS.upload) {
      await expect(page.locator(`[data-card="${code}"]`)).toBeVisible();
    }
  });

  test("a ZIP dropped on the box is imported", async ({ page }) => {
    const dialog = await openImport(page);
    await drop(page, dialog.getByRole("button", { name: /Drop the ZIP here/ }), [zip()]);
    await expect(dialog.getByText("(this exact ZIP was imported before)")).toBeVisible();
  });

  test("a ZIP dropped anywhere on the page is imported", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("[data-card]").first()).toBeVisible();
    const main = page.locator("main");
    // While it is dragged over, the overlay says it can be dropped.
    await main.dispatchEvent("dragenter", {
      dataTransfer: await page.evaluateHandle(() => {
        const dt = new DataTransfer();
        dt.items.add(new File(["x"], "upload.zip", { type: "application/zip" }));
        return dt;
      }),
    });
    await expect(page.getByText("Drop to import")).toBeVisible();
    await drop(page, main, [zip()]);
    await expect(page.getByText("Drop to import")).toBeHidden();
    const dialog = page.getByRole("dialog", { name: "Import your Instagram export" });
    await expect(dialog.getByText(/Imported upload\.zip via .*: 0 new/)).toBeVisible();
  });

  test("a file that isn't a ZIP is refused, and not sent", async ({ page }) => {
    const dialog = await openImport(page);
    let sent = false;
    page.on("request", (r) => { if (r.url().endsWith("/api/import")) sent = true; });
    await drop(page, dialog.getByRole("button", { name: /Drop the ZIP here/ }), [notZip()]);
    await expect(dialog.getByText("notes.txt isn't a ZIP")).toBeVisible();
    expect(sent).toBe(false);
  });

  test("the import dialog keeps focus, and Esc gives it back", async ({ page }) => {
    const dialog = await openImport(page);
    const opener = page.getByRole("button", { name: "Import", exact: true });
    await expect(dialog).toBeVisible();
    // Focus starts inside, and Tab never leaves for the page behind.
    for (let i = 0; i < 12; i++) {
      expect(await dialog.evaluate((d) => d.contains(document.activeElement))).toBe(true);
      await page.keyboard.press("Tab");
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});

test.describe("as Bob", () => {
  test.use({ storageState: PEOPLE.bob.state });

  test("what Alice imported is not in his library", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("[data-card]").first()).toBeVisible();
    for (const code of REELS.upload) {
      await expect(page.locator(`[data-card="${code}"]`)).toHaveCount(0);
    }
  });
});
