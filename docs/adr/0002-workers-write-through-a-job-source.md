# 0002. Workers write through a job source, not raw SQL

- **Status:** Accepted
- **Date:** 2026-10-25 (#72), 2026-10-26 (#35)
- **Issue:** #72, #35; `app/jobs/queue.py`, `app/jobs/protocol.py`

## Context

The always-on worker (#72) needed somewhere to claim work and report back. A
worker on the server's host could use the database directly. A worker on a
desktop couldn't ([0001](0001-sqlite-not-postgres.md)), and #35 was already
planned.

## Decision

The worker loop talks only to a `JobSource`: `claim`, `release`, `renew`,
`reclaim`, `check_in`, `check_out`. `LocalJobs` implements it over SQLite on the
same host, and `HttpJobs` over the server's `/api/worker/*` routes. A remote
worker never writes SQL. It posts one result per reel, which the server checks
(`protocol.parse`) and records with the stage's own `record` and
`record_failure` functions, the same ones a local run uses. The server then
releases the leases through its own `LocalJobs`.

## Alternatives

- **Workers write SQL, local or tunnelled.** It is impossible remotely, and
  even locally every worker would have to agree on the schema.
- **A separate result format with its own writer.** Two ways to record a
  transcript, which would drift apart.

## Consequences

- One loop for local and remote workers. Leases, readiness and the attempts
  cap are written once, and a local and a remote worker can share a stage
  without colliding.
- The server is the only writer to the database, so it can refuse what is
  wrong: another release's stage version (409), a malformed result (422), or a
  result for a reel whose lease has run out (dropped).
- The protocol has a version (`PROTOCOL`), and both sides must run the same
  release.
- Each stage's per-reel work is a function that takes a row and returns a value
  (`transcribe_one`, `tag_one`…), so it can run on either side.
