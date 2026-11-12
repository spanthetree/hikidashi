import { expect, test } from "@playwright/test";
import { FAKE_AI, PEOPLE, REELS } from "./env";

/* Hosted AI (#125): Bob sets up his own API against a fake OpenAI-compatible
   server, tests the connection, switches it on with the backlog, watches his
   reels get processed, and switches it off. */

const STAGES = ["Transcription", "On-screen text", "Tags", "Recipes"];
const MODELS = { Transcription: "whisper-1", "On-screen text": "vision", Tags: "chat", Recipes: "chat" };

test.describe("hosted AI", () => {
  test("Bob's own API processes his reels, and stops when switched off", async ({ browser }) => {
    test.setTimeout(90_000);
    const bob = await browser.newPage({ storageState: PEOPLE.bob.state });
    await bob.goto("/account");
    const mine = bob.locator("#ai");
    await expect(mine.getByRole("heading", { name: "AI providers" })).toBeVisible();
    // One choice each. Nothing is set up yet, so the default is workers.
    await expect(mine.getByText("Your reels follow the system default: workers.")).toBeVisible();
    await expect(mine.getByRole("radio")).toHaveCount(3);
    await mine.getByRole("radio", { name: "Hosted AI" }).check();
    await expect(mine.getByText(/Hosted AI is chosen, and not started yet/)).toBeVisible();

    for (const stage of STAGES) {
      await mine.getByLabel(`Provider for ${stage}`).selectOption("compatible");
      const row = mine.locator("tr", { hasText: stage });
      await row.getByLabel("Base URL").fill(`http://127.0.0.1:${FAKE_AI}/v1`);
      await row.getByLabel("Base URL").blur();
      await expect(bob.getByText("Saved.")).toBeVisible();
      await row.getByLabel(`Model for ${stage}`).fill(MODELS[stage as keyof typeof MODELS]);
      await row.getByLabel(`Model for ${stage}`).blur();
    }

    // Write-only: shown back masked, never whole.
    const key = "sk-e2e-0123456789abcdefXYZW";
    await mine.getByLabel("OpenAI-compatible URL key").fill(key);
    await mine.getByRole("button", { name: "Save" }).first().click();
    await expect(mine.getByText("sk-…XYZW")).toBeVisible();
    expect(await bob.content()).not.toContain(key);

    await mine.getByLabel("Monthly budget").fill("5");
    await mine.locator("form", { has: bob.getByLabel("Monthly budget") }).getByRole("button", { name: "Save" }).click();

    await mine.getByRole("button", { name: "Test connection" }).click();
    await expect(mine.locator(".ai-test", { hasText: "OK" })).toHaveCount(4);

    await mine.getByRole("button", { name: "Turn on" }).click();
    const ask = mine.getByRole("dialog", { name: "Reels waiting" });
    await expect(ask.getByText(/reels? waiting, about/)).toBeVisible();
    await ask.getByRole("button", { name: "Process these too" }).click();
    await expect(mine.getByText("Your reels are processed by your hosted AI.")).toBeVisible();

    // The server's API host picks them up: every one of Bob's reels tagged.
    await expect
      .poll(async () => {
        const r = await bob.request.get("/api/me/pipeline");
        const stages = (await r.json()).stages as { stage: string; waiting: number }[];
        return stages.filter((s) => ["asr", "ocr", "tag", "recipe"].includes(s.stage))
          .reduce((n, s) => n + s.waiting, 0);
      }, { timeout: 60_000 })
      .toBe(0);
    const detail = await (await bob.request.get(`/api/items/${REELS.bob[0]}`)).json();
    expect(JSON.stringify(detail)).not.toContain(key);

    await mine.getByRole("button", { name: "Turn off" }).click();
    await expect(mine.getByText(/Hosted AI is chosen, and not started yet/)).toBeVisible();
    await mine.getByRole("radio", { name: "System default" }).check();
    await expect(mine.getByText("Your reels follow the system default: workers.")).toBeVisible();
    await bob.close();
  });

  test("an admin's one choice is the system default", async ({ browser }) => {
    const alice = await browser.newPage({ storageState: PEOPLE.alice.state });
    await alice.goto("/account");
    const ai = alice.locator("#ai");
    await expect(alice.getByRole("heading", { name: "AI providers" })).toHaveCount(1);
    await expect(ai.getByRole("radio")).toHaveCount(2);
    await expect(ai.getByText("Workers are the system default.")).toBeVisible();
    await ai.getByRole("radio", { name: "Hosted AI" }).check();
    await expect(ai.getByLabel("Provider for Transcription")).toBeVisible();
    await ai.getByRole("radio", { name: "Workers only" }).check();
    await expect(ai.getByText("Workers are the system default.")).toBeVisible();
    await alice.close();
  });
});
