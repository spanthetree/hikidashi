import { defineConfig, devices } from "@playwright/test";
import { FAKE_AI, FIRST_RUN, SEEDED, SETUP_TOKEN, UPLOAD } from "./e2e/env";

/* Browser tests against the real server: FastAPI serving the built front end
   (app/web), on a throwaway library that tests/seed.py builds. `npm run e2e`
   builds first; docs/testing.md has the rest.

   Two servers, both with accounts on. SEEDED has Alice and Bob signed up;
   FIRST_RUN is a new library nobody has claimed yet. */

// The repository's Python: `uv run python` here, plain `python` in CI.
const python = process.env.HIKIDASHI_PYTHON ?? "uv run python";
const CI = !!process.env.CI;

const server = (port: number, args: string) => ({
  command: `${python} -m tests.serve --port ${port} ${args}`,
  cwd: "..",
  url: `http://127.0.0.1:${port}/api/auth`,
  // Never someone else's server: each run starts from the same library.
  reuseExistingServer: false,
  stdout: "pipe" as const,
  timeout: 60_000,
});

export default defineConfig({
  testDir: "e2e",
  // The tests share a server, so they run one at a time; the suite is small.
  workers: 1,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  reporter: CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: [
    server(SEEDED, `--upload frontend/${UPLOAD} --fake-ai ${FAKE_AI}`),
    server(FIRST_RUN, `--empty --setup-token frontend/${SETUP_TOKEN}`),
  ],
  projects: [
    // Signs Alice and Bob in once; the tests reuse their cookies.
    {
      name: "sign-in",
      testMatch: /auth\.setup\.ts/,
      use: { baseURL: `http://127.0.0.1:${SEEDED}` },
    },
    {
      name: "chromium",
      testIgnore: /first-run/,
      dependencies: ["sign-in"],
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${SEEDED}` },
    },
    {
      name: "first-run",
      testMatch: /first-run\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${FIRST_RUN}` },
    },
  ],
});
