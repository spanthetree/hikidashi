/* What the config and the tests both need to know. The people and reels are
   tests/seed.py's: keep the two in step. */

export const SEEDED = 8093;
export const FIRST_RUN = 8094;
export const FAKE_AI = 8095;  // a fake OpenAI-compatible API, beside SEEDED (#125)

// Relative to frontend/, where Playwright runs.
export const OUT = "test-results/e2e";
export const UPLOAD = `${OUT}/upload.zip`;           // reels nobody has yet
export const SETUP_TOKEN = `${OUT}/setup-token`;     // FIRST_RUN's, which it only logs

export const PEOPLE = {
  alice: { username: "alice", password: "alice-password", state: "e2e/.auth/alice.json" },
  bob: { username: "bob", password: "bob-password", state: "e2e/.auth/bob.json" },
} as const;

export const REELS = {
  shared: "SHAREDreel01",
  alice: ["ALICEramen01", "ALICEtokyo01", "ALICEkyoto01", "ALICEdrift01", "ALICEsimrc01",
          "ALICEgym0001", "ALICEdiy0001", "ALICEcode001", "ALICEnotag01", "ALICEfail001"],
  bob: ["BOBbudget001", "BOBgaming01"],
  upload: ["UPLOADnew001", "UPLOADnew002"],
} as const;
