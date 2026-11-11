# 0008. Mutation testing (mutmut) dropped for now

- **Status:** Accepted
- **Date:** 2026-10-24
- **Issue:** #39; follow-up #70; [testing.md](../testing.md)

## Context

#39 set out to find where the tests are weak, with Playwright browser tests and
mutation testing with mutmut.

## Decision

Set mutmut aside. The Python suite stays on stdlib `unittest`. The effort went
into static checks instead: `ruff check`, `ruff format` (line length 100), and
`mypy --strict`, all in CI.

## Alternatives

- **mutmut now.** It needs pytest as its runner, and its repeated runs tripped
  Hypothesis's health checks in the property tests. Adopting it meant
  changing the test runner for the sake of one tool.
- **Gradual typing** instead of `--strict`. Strict from the start found real
  bugs (a crash in `hikidashi user invite`, a route shadowing `collections`)
  and leaves nothing to tighten later.

## Consequences

- No measure of how many mutants the tests kill. Coverage is reported, with no
  threshold.
- #70 holds what was learnt, for mutation testing as a nightly job later.
