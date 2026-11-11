# CLAUDE.md

Follow [CONTRIBUTING.md](CONTRIBUTING.md). Four points matter most:

- Make every change on a new branch cut from a freshly pulled `master`. Never
  commit to `master` directly.
- One PR per issue, with each phase as its own commit.
- Before running `gh pr create`, make a final commit on the branch that updates
  `CHANGELOG.md` and `README.md` and bumps the version everywhere it appears.
  CONTRIBUTING.md lists the files.
- Keep the architecture docs true. A change to the schema, the stages, the
  worker and lease rules, the remote protocol, the images, search ranking,
  auth or scoping, or anything that leaves the machine updates
  `docs/architecture.md`, `docs/operation.md` or `docs/adr/` in the same PR.
  The table in CONTRIBUTING.md ("Keeping the docs and diagrams true") says
  which file. Regenerate the generated diagrams with
  `uv run python scripts/make_diagrams.py` and never hand-edit them; edit the
  animated SVGs in that script, not in the `.svg`. Record the README animation
  and the worker terminal on the demo library (`scripts/demo_library.py`),
  never on the user's own library.
