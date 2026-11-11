# 0017. The version is written once, in pyproject.toml

- **Status:** Accepted. Supersedes [0012](0012-nas-installs-templates-and-yaml.md)'s
  pinned TrueNAS release.
- **Date:** 2026-11-12
- **Issue:** #123

## Context

Every release set the same version by hand in seven files and ten places:
`pyproject.toml`, `uv.lock`, `frontend/package.json`, both entries of
`frontend/package-lock.json`, the README badge, and the three image tags in
the TrueNAS file. Nothing at run time read any of them. The release workflow
reads `pyproject.toml` alone. Two branches open at once each claimed the next
number, and every merge between them conflicted in all seven files.

## Decision

- **`pyproject.toml` holds the version.** The release workflow already reads
  it there, and `CHANGELOG.md` has a section for it.
- **`uv.lock` follows by `uv lock`**, not by hand. `tests/test_deploy.py`
  fails while it disagrees with `pyproject.toml`.
- **The frontend has no version.** It is a private package that nothing
  publishes, so `package.json` and its lockfile carry none.
- **The README badge reads the latest GitHub release** from shields.io.
- **The TrueNAS file follows `:latest`**, as the Unraid templates and the
  compose files' default already do. TrueNAS pulls the three images of one
  app together, so the server and the worker still land on the same release.
  To hold one, a user replaces `latest` in all three.

`tests/test_deploy.py` fails if a copy of the version comes back in any of
these files.

## Alternatives

- **A bump script** that writes all ten places. It keeps the copies, and the
  merge conflicts between branches, and only saves the typing.
- **A dynamic version** (`dynamic = ["version"]` from a git tag or a file).
  The workflow tags a release only after it publishes, from the version it
  read, so the tag cannot also be the source.
- **Keep the TrueNAS pin, bumped by the release workflow.** The workflow
  would have to commit to `master` after the merge, and a pasted file still
  goes stale the day after.

## Consequences

- A release is: the version in `pyproject.toml`, `uv lock`, and the changelog
  section.
- The badge shows the last published release, not the branch's version, and
  shows nothing while the repository is private.
- A TrueNAS install that keeps `:latest` moves to a new release when the app
  pulls its images again, not when a user edits the YAML.
