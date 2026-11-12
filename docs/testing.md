# Testing

How Hikidashi is tested and checked, how to run each check, and what CI runs when.
[Back to the README](../README.md) · [Development](development.md)

| Kind | Where | Runs on | Local command |
|------|-------|---------|---------------|
| Python unit and API tests | `tests/` | every PR | `python3 -m unittest discover -s tests -t .` |
| Property tests (Hypothesis) | `tests/test_properties.py` | every PR | the same, after `uv sync --group test` |
| Python coverage | `pyproject.toml` | every PR, report only | `uv run --group test coverage run -m unittest discover -s tests -t . && uv run --group test coverage report` |
| Frontend unit tests (vitest) | `frontend/src/**/*.test.ts` | every PR | `npm test --prefix frontend` |
| Frontend coverage | `frontend/vite.config.ts` | every PR, report only | `npm run coverage --prefix frontend` |
| Browser tests (Playwright) | `frontend/e2e/` | every PR, Chromium | `npm run e2e --prefix frontend` |
| Lint and format (ruff) | `pyproject.toml` | every PR | `uv run --group lint ruff check && uv run --group lint ruff format --check` |
| Types (mypy, strict) | `pyproject.toml` | every PR | `uv run --group lint mypy` |
| Frontend types (svelte-check) | `frontend/tsconfig.json` | every PR | `npm run check --prefix frontend` |

## Dependencies

The shipped core is stdlib only, and `python3 -m unittest` still runs the whole
Python suite on a bare checkout: the tests that need something more skip
without it. The tools are two uv dependency groups that neither a plain
`uv sync` nor the Docker images (`--no-dev`) install. Name the extras with them,
as `uv sync` removes whatever the command line leaves out:

```bash
uv sync --extra server --extra worker --group test --group lint
```

`unittest` is the runner; nothing needs pytest. The frontend's test tools
(vitest, Playwright, fast-check) are ordinary `devDependencies`. Frontend
commands need Node 22.17 or later.

## The seeded library

`tests/seed.py` builds a small, deterministic library: two people, Alice (user
1, the admin) and Bob (invited), a reel they share and their own besides, tags
from hashtags, two collections, a recipe, transcripts, a failed reel, and
placeholder thumbnails and video. It goes through the real import, hashtag pass
and recipe store, and writes what the fetch and transcribe stages would have,
so nothing needs yt-dlp, Whisper, Ollama or an embedding model; search runs in
its keyword-only fallback. `exports/upload.zip` holds two reels nobody has yet.

```bash
python3 -m tests.seed /tmp/demo      # build one to look at
```

The placeholder video, `tests/assets/reel.mp4`, is VP9: Playwright's Chromium
has no H.264, which real reels are. Use it for new tests in place of setup
written out again in each file (`tests/test_seed.py` shows how).

`tests/serve.py` runs the real server on a throwaway copy of it, in a new
temporary folder, with accounts on, and the locator and geocoder off:

```bash
uv run python -m tests.serve --port 8093            # Alice and Bob
uv run python -m tests.serve --port 8094 --empty    # a first run: nobody yet
```

Sign in as `alice` / `alice-password` or `bob` / `bob-password`. It serves the
front end from `app/web`, so build that first.

## Browser tests

[Playwright](https://playwright.dev) in `frontend/e2e/`, against the real
FastAPI app serving the built front end. `npm run e2e` builds, then
`playwright.config.ts` starts two servers with `tests/serve.py`: the seeded
library on port 8093 and an unclaimed one on 8094. A setup project signs Alice
and Bob in through the page once and keeps their cookies (`e2e/.auth/`) for the
tests that run as them.

```bash
cd frontend
npx playwright install chromium     # once
npm run e2e                         # build, then every test
npx playwright test isolation       # one file, against the last build
npx playwright test --ui            # watch it, step by step
npx playwright show-report          # after a CI failure, from its artifact
```

The servers run `uv run python` by default; set `HIKIDASHI_PYTHON` to another
interpreter with the web dependencies and argon2-cffi (CI uses `python`). Each
run starts fresh servers, never one already running, so every run sees the
same library. The tests share them and run one at a time.

What is covered so far:

| Flow | File |
|------|------|
| First run: a library is claimed with the setup link and opens on the import box, its link to Instagram's export page in a new tab; a wrong token is refused; the owner signs out and back in; an invite makes one account, once | `first-run.spec.ts` |
| Isolation ([#12](https://github.com/spanthetree/hikidashi/issues/12)): Bob's grid, sidebar and search hold none of Alice's reels; her deep link opens nothing; her reel and its files are a 404, like a reel nobody has; the reel they share opens for both | `isolation.spec.ts` |
| Importing an export ([#105](https://github.com/spanthetree/hikidashi/issues/105)): chosen in the import box, dropped on its zone, or dropped anywhere on the page; a file that isn't a ZIP refused without being sent; the toolbar's dialog keeps focus, closes on Esc and gives focus back; what one person imports lands in their library only | `import.spec.ts` |

Selectors are what a person sees (roles, labels, text) where that is stable,
and `data-card` for cards. The English strings come from
`frontend/messages/en.json`; a test that breaks on a reworded message should
be updated with it.

To check that a test can fail, break what it guards for a moment. With
`selection()` in `app/api/main.py` reading user 1's library for everyone,
`isolation.spec.ts` fails on Bob's grid; with `library.mine` ignoring the user,
it fails on Alice's thumbnail. Both were tried while writing it.

**In CI** the `e2e` job runs inside Playwright's own image,
`mcr.microsoft.com/playwright`, which already has Chromium and the system
packages it needs, so nothing is installed from Ubuntu's mirrors ([#131](https://github.com/spanthetree/hikidashi/issues/131)). Its
tag names a Playwright release, and the browsers in it only work with that
release of `@playwright/test`: when one moves in `frontend/package.json`, move
the `container:` tag in `.github/workflows/tests.yml` with it.
`tests/test_ci.py` fails while the two disagree.

**Cost:** locally, about 40 s of tests after a 6 s build. In CI the `e2e` job
takes about 1 min 40 s all told, and runs alongside
the others (`python` and `lint` about 30 s, `frontend` 40 s). It is the
slowest, so a pull request now waits about a minute longer for its checks.

When a test fails, Playwright keeps a trace, a screenshot and a video under
`frontend/test-results/`; CI uploads them, with the HTML report, as the
`playwright` artifact.

## Coverage

Report only: no threshold fails a build. It shows where tests are thin.
Python's goes to the CI job summary, branch coverage included; the frontend's
is printed in the job log and uploaded as `frontend-coverage` (HTML).

Where it stood when this was set up (October 2026):

- **Python: 76% of lines and branches.** Under 50% are the `__main__.py`
  command-line entry points (0%; `tests/test_cli.py` drives them through
  `app/cli.py` in a subprocess, which coverage does not follow),
  `app/search/eval.py` (31%) and `app/asr/transcriber.py` (49%), which need the
  real models. The pure modules are 93–100%.
- **Frontend: 14% of statements.** The `lib/` helpers the unit tests cover are
  near 100% (`filters.ts`, `caption.ts`, `transcript.ts`); every component is
  0%, because nothing renders one outside the browser tests, which coverage
  does not see.

## Property tests

Hypothesis (`tests/test_properties.py`) and fast-check
(`frontend/src/lib/filters.property.test.ts`) generate inputs and look for one
that breaks a rule:

- `fts_query` and `phrase_query` give valid FTS5 syntax for any text,
  including FTS operators typed in, and nothing for text without words.
- A shortcode comes back out of every permalink shape: with or without `www`,
  a locale, a trailing slash or a query string.
- The parser never raises on any JSON, and keeps a record's caption.
- Any text garbled the way Instagram's export garbles it is repaired.
- Filter URLs, for any query string: read, written and read again come back the
  same, are written the same way every time, and never write a default.

None found a bug when they were added.

## Lint, format and types

[ruff](https://docs.astral.sh/ruff/) lints and formats the Python;
[mypy](https://mypy.readthedocs.io) type-checks it. Both are configured in
`pyproject.toml`, and the `lint` CI job fails on any finding.

```bash
uv run --group lint ruff format              # format everything
uv run --group lint ruff check --fix         # lint, fixing what is safe to
uv run --group lint mypy                     # app, tests and scripts
```

**Formatting** is `ruff format` at 100 columns, the width the code was
already written to: one pass in October 2026 reformatted every file, and
`.git-blame-ignore-revs` lists that commit so blame looks past it (`git
config blame.ignoreRevsFile .git-blame-ignore-revs` for the command line;
GitHub reads it itself).

**Lint rules:** pycodestyle (line length included), pyflakes, import order,
bugbear, pyupgrade to 3.12, simplify, mccabe complexity and ruff's own. Off,
with the reason beside each in `pyproject.toml`: B008 (FastAPI's `Query()`
defaults), SIM117 (nested `with`s around mocks), SIM118 (on a `sqlite3.Row`,
`k in row` looks at the values, not the keys), SIM905 (word lists split from
one string) and the ambiguous-Unicode rules (captions and Japanese text are
full of it).

**Complexity** is capped at 15, the most tangled function when the cap went
in (`import_export`). Three are above 12: `import_export`, `parse_collections`
and `transcribe_all`. Lower the cap as they are split up.

**Types:** `mypy --strict` over `app/`, `scripts/` and `tests/`. Tests are
checked, bodies and all, but a test method needs no annotations, and a helper
returning a row from `fetchone()` needn't cast it. `tests.fixtures.must()`
unwraps a result a test expects to be there. The models' packages
(`faster_whisper`, `ctranslate2`, `fastembed`, `sqlite_vec`, `nvidia`) are
imported lazily and untyped, so mypy ignores them; CI installs the web layer,
argon2, numpy and hypothesis for it to read the rest against.

Strict typing found, besides missing annotations: `hikidashi user invite`
crashing when no enabled admin existed, a route function shadowing the
`collections` module, a test shadowing `TestCase.run` and another overriding
`TestCase.fail`.

Mutation testing (mutmut) was tried and set aside for now: it needs pytest,
and its repeated runs tripped Hypothesis's health checks. [#70](https://github.com/spanthetree/hikidashi/issues/70) has what was
learnt, for when it comes back as a nightly job.

## Not yet

Each has a follow-up issue:

- The remaining browser flows: search and filters through the URL, the
  detail sheet, editing tags and recipes, 日本語, reduced motion, sharing,
  and semantic search with the embedder ([#66](https://github.com/spanthetree/hikidashi/issues/66))
- WebKit, Firefox and phone viewports ([#67](https://github.com/spanthetree/hikidashi/issues/67))
- Accessibility (axe) and visual regression checks ([#68](https://github.com/spanthetree/hikidashi/issues/68))
- Svelte component tests, and StrykerJS for `frontend/src/lib/` ([#69](https://github.com/spanthetree/hikidashi/issues/69))
- Mutation testing with mutmut, nightly ([#70](https://github.com/spanthetree/hikidashi/issues/70))
