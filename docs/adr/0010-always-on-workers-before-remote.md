# 0010. Always-on workers before remote ones

- **Status:** Accepted
- **Date:** 2026-10-25
- **Issue:** #72, then #35

## Context

Every stage ran only when someone typed its command, so new reels sat
unprocessed on an always-on server. Separately, #35 wanted the heavy stages on
a GPU desktop. Both need a way for a long-running process to find work, hold
it, and survive dying with it.

## Decision

Build the always-on worker first (#72), on a single host. A job model on top of
each stage's pending query: **leases** (claimed for 5 minutes, renewed every 30
s, so a dead worker's reels come back), **readiness** (a reel is offered to a
stage only when its upstream stages are done with it), and an **attempts cap**
(3 tries, with a 60 s and 120 s pause between them, so a reel that crashes the
worker can't crash it forever). Then #35 adds the HTTP job source to the same
loop ([0002](0002-workers-write-through-a-job-source.md)).

## Alternatives

- **Remote first.** It would have built the job model and the network
  protocol at once, and left single-host users with manual runs meanwhile.
- **A message queue** (Redis, RabbitMQ). The work is already a query, so a
  queue would be a second copy of the truth to keep in step, and another
  service to run.
- **Cron running the stage commands.** No leases, so two runs overlap. No
  readiness, so a reel gets tagged from its caption before its transcript
  exists, and tagged again after.

## Consequences

- A reel is fetched, transcribed, tagged and embedded on its own after an
  import, which wakes the workers through `data/run/worker.wake`.
- Remote workers got the same queue for the cost of an HTTP job source.
- The stage commands still work by hand, and with `HIKIDASHI_SERVER` set they
  claim from the server like a worker (`--once`).
