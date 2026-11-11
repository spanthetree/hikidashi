# 0001. SQLite, not Postgres

- **Status:** Accepted
- **Date:** 2026-10-26
- **Issue:** #35 (full comparison), [remote-workers.md](../remote-workers.md#the-options)

## Context

Splitting the heavy stages onto a GPU desktop (#35) put the database in
question. A worker on another machine can't open a SQLite file over the network:
SQLite over NFS or SMB is a corruption path. Postgres would let every worker
connect directly.

## Decision

Keep SQLite: one file, on a local disk, written only by processes on the
server's host. Workers on other machines reach it through the server's HTTP
API ([0002](0002-workers-write-through-a-job-source.md)).

## Alternatives

- **Postgres.** Workers connect directly. It would mean rewriting the
  SQLite-specific parts (FTS5 and its triggers, `bm25`, sqlite-vec, the JSON
  helpers), re-tuning search against the eval set, running a database
  service, and needing one for every test run. And it still wouldn't stop a
  worker from writing a vector from the wrong model, or keep workers and server
  on one schema.
- **Ship the database around** (a replica on the desktop, changesets back).
  Merging changesets makes conflicts Hikidashi's problem, for no gain over an
  API.
- **Remote Ollama only.** The server's worker calls the desktop's Ollama. It
  needs no code, but only tagging moves; transcription stays on the server's
  CPU. It was the baseline while the API was built, and retired in #86.

## Consequences

- One file to back up, and nothing to run beside the server. Tests open an
  in-memory database with the real schema.
- `data/` must be on local block storage: not NFS, not SMB, not Unraid's
  `/mnt/user` FUSE share ([architecture.md](../architecture.md#storage-layout)).
- One writer host. The server runs uvicorn with `--workers 1`, and the local
  workers share the file through WAL and `BEGIN IMMEDIATE` claims.
- Worth revisiting only if the server needs several replicas (k8s) or many
  people use it at once.
