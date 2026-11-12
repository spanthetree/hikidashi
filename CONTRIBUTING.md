# Contributing to Hikidashi

Thanks for wanting to help. Bug reports, ideas, translations, docs fixes and
code are all welcome.

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). A security problem goes to a
[private report](SECURITY.md), not an issue.

## Ways to help

- **Report a bug** or **suggest a feature** with the
  [issue forms](https://github.com/spanthetree/hikidashi/issues/new/choose).
- **Pick up an issue.** Ones labelled
  [`good first issue`](https://github.com/spanthetree/hikidashi/labels/good%20first%20issue)
  or [`help wanted`](https://github.com/spanthetree/hikidashi/labels/help%20wanted)
  are a good start. Say in the issue that you're on it, so no one else starts too.
- **For anything larger,** a new feature, a new dependency, or a change to the
  schema, the pipeline or the remote protocol, open an issue or comment on the
  existing one before you write much code. The
  [decision records](docs/adr/README.md) explain why things are the way they
  are; a change that goes against one needs a new record.
- **Translate the UI.** See [Translations](#translations).

## Setting up

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22.17 or later,
and FFmpeg. Docker is only needed to work on the images.

```bash
git clone https://github.com/spanthetree/hikidashi.git && cd hikidashi
uv sync --extra server --extra worker --group test --group lint
npm ci --prefix frontend
./hikidashi start          # the server and a worker, on http://localhost:8080
```

To work on the web app with live reload, run the API and Vite side by side
([development.md](docs/development.md#the-front-end)).

For a library to try things on, use the demo library rather than your own
saves: `scripts/demo_library.py` builds one from freely licensed media and runs
it on port 8090 ([development.md](docs/development.md#recording-the-demo)).
Anything you post (screenshots, logs, test data) should come from it, or be
made up.

[docs/development.md](docs/development.md) covers the rest of the setup, and
[docs/architecture.md](docs/architecture.md) and
[docs/operation.md](docs/operation.md) how the pieces fit together.

## Checks

CI runs all of these on every pull request. To run them yourself:

```bash
python3 -m unittest discover -s tests -t .   # Python tests; stdlib only, no network
uv run --group lint ruff format              # format
uv run --group lint ruff check --fix         # lint
uv run --group lint mypy                     # types, strict
npm run check --prefix frontend              # Svelte and TypeScript
npm test --prefix frontend                   # web app unit tests
npm run e2e --prefix frontend                # browser tests (Playwright)
```

[docs/testing.md](docs/testing.md) explains each, what is skipped without
optional packages, and the seeded library the tests share. A change comes with
tests for what it changes.

## Translations

The UI ships in English and Japanese. Every string is a message in
`frontend/messages/en.json`, with the translation under the same key in the
other languages' files. Fixing a translation is a one-file pull request. To
add a language, follow the four steps in
[development.md](docs/development.md#translations). `npm test` catches a
missing key or a mismatched `{input}`.

## Licensing your contribution

Hikidashi is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE): anyone may use, copy, change
and share it for any noncommercial purpose. Patches, issues and pull requests
are welcome on the same footing as in an open-source project.

So that the maintainer can keep every version of Hikidashi under one license,
and offer it under other terms later (a hosted version, say), contributions
come in under a wider grant than the one they go out under. By opening a pull
request or otherwise submitting a contribution, you agree that:

- you wrote it, or otherwise have the right to submit it;
- you license it to the maintainer, David Woodruff, under the
  [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0), as well as
  to everyone under the project's license; and
- you keep the copyright in it.

If you can't agree to that, for example because an employer owns your work,
say so in the pull request before it is merged.

## Branches and pull requests

- Make every change on a new branch. Pull `master` first, then branch off it
  (`git checkout master && git pull && git checkout -b <branch>`). Never commit
  straight to `master`.
- One pull request per issue. Land each phase of the work as its own commit,
  and put the whole issue in a single PR.
- Before you open the PR, the branch's last change must:
  - **Update `CHANGELOG.md`.** Add a dated `## [x.y.z] — YYYY-MM-DD` section
    under `[Unreleased]`, following
    [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
  - **Update `README.md`** wherever features, setup or configuration changed.
  - **Bump the version** under [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
    It is written once, in `pyproject.toml`; then run `uv lock` so `uv.lock`
    agrees ([ADR 0017](docs/adr/0017-version-written-once.md)).
    `tests/test_deploy.py` fails if they disagree, or if a copy turns up
    anywhere else.
  - **Record the evals** for the new version:
    `uv run python scripts/evals.py --record --note "what moved them"` (leave
    the note empty when nothing did), then
    `uv run python scripts/make_diagrams.py`. `tests/test_evals.py` fails if
    the last row is not this version, or not what the code scores
    ([docs/eval.md](docs/eval.md)).

  Not sure which version a change should be? A fix is a patch, a feature a
  minor; ask in the PR, and the maintainer will settle it before merging.
- **Merging is releasing.** Once a version bump lands on `master`,
  `.github/workflows/release.yml` publishes the images to GHCR, then tags the
  commit `vX.Y.Z` and makes a GitHub release from its changelog section.
  Don't tag by hand. If the images fail, nothing is tagged: fix it, or run
  the workflow again from the Actions tab. To try a branch's images first,
  run the **images** workflow on it, which publishes `edge`.

## Keeping the docs and diagrams true

The architecture docs describe the code as it is. A change that moves one of
the things below updates the matching doc **in the same PR**, in the commit
that makes the change or one right after it.

| When a change… | Update |
|---|---|
| adds, drops or renames a table, column, key or index in `app/schema.sql` | run `uv run python scripts/make_diagrams.py` (the ER diagrams); a new table also goes into one of its `GROUPS`; the prose in [architecture.md](docs/architecture.md#data-model) if it changes what a table means |
| adds or reorders a stage, or changes what a stage waits for, skips or stamps (`app/jobs/queue.py`) | run the script (the stage graph); [operation.md](docs/operation.md#reel-lifecycle); the `pipeline()` SVG in the script if the stages it draws change |
| changes leases, readiness, the attempts cap, backoff, batch sizes or idle/unload timings | [operation.md](docs/operation.md#the-worker-loop); the `pipeline()` and `leases()` SVGs if the numbers they show change |
| changes the remote protocol: a route, a check, what is sent, `protocol.REMOTE` | [operation.md](docs/operation.md#the-remote-worker-protocol), the security section of [architecture.md](docs/architecture.md#security-boundaries), the `remote()` SVG, and [remote-workers.md](docs/remote-workers.md) |
| changes which image or container runs what, or a `HIKIDASHI_LOCAL_STAGES` default (`Dockerfile`, compose files) | [architecture.md](docs/architecture.md#containers-and-images) and its deployment topologies |
| changes a container's command, environment, user, ports or volumes in the compose files | the same in the NAS install files, `deploy/unraid/*.xml` and `deploy/truenas/hikidashi.yaml`, and [install-unraid.md](docs/install-unraid.md) or [install-truenas.md](docs/install-truenas.md) if what a user sets changes |
| changes how search ranks: a list, a weight, a threshold, a tier | the table in [operation.md](docs/operation.md#a-search-request), the `search()` SVG if what it shows changes, and [search.md](docs/search.md) with the eval numbers; `scripts/evals.py --check` must pass, or the PR says why the demo scores fell |
| changes import, sign-in, sessions, CSRF, proxy trust, worker tokens or per-user scoping | the matching sequence in [operation.md](docs/operation.md) and [architecture.md](docs/architecture.md#security-boundaries) |
| adds a call to anything outside the machine, or a new place data is kept | the context table in [architecture.md](docs/architecture.md#context), or its storage layout |
| changes what the library looks like or how it is used, enough that the README animation no longer shows the app | re-record it on the demo library: `node scripts/record-demo.mjs hero` (and `tour`), and retake the stills with `node scripts/screenshots.mjs`, in `frontend/`, as in [development.md](docs/development.md#recording-the-demo) |
| changes what a worker logs | re-record the terminal: `uv run python scripts/demo_library.py cast`, then run `make_diagrams.py` |
| makes or reverses a decision argued out in an issue | a new record in [docs/adr/](docs/adr/README.md). Don't rewrite an accepted record: add one that supersedes it, and mark the old one **Superseded by** |

How:

- **Generated diagrams** (ER, stage graph, the animated SVGs) come from
  `scripts/make_diagrams.py`. Never hand-edit the output: change the code or
  the script, then run it. `tests/test_diagrams.py` fails in CI when the
  output is stale.
- **Hand-written Mermaid** (context, containers, layouts, sequences, state
  machines) is edited in the Markdown. Review it as GitHub renders it in the
  PR.
- **Animated SVGs** are changed in their function in the script, never in the
  `.svg`. Keep #88's rules (dark mode, reduced motion showing the end state, a
  readable still frame, no rasters, an accurate `aria-label`), and check the
  result in a browser in both themes. [development.md](docs/development.md#regenerating-the-diagrams)
  has the details.
- **Recordings** (the README animation, the tour, the worker's terminal) are
  made on the demo library from `scripts/demo_library.py`, never on your own:
  its media are freely licensed and credited, and your saves are not yours to
  publish. They are not checked in CI, so a reviewer looks at them.
- If a change needs none of this, say so in the PR description, so a reviewer
  knows it was considered.
