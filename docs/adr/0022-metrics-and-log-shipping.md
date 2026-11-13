# 0022. Metrics and log shipping: opt-in, pulled, bounded, redacted

- **Status:** Accepted
- **Date:** 2026-11-13
- **Issue:** #149

## Context

The numbers an admin wants to graph and alert on (the backlog per stage,
workers gone quiet, failures, hosted AI spend, the database's size) already
exist, but only as JSON for the Pipeline page. Logs go to stdout and a file
per process, which the Logs page reads. Nothing could be scraped, and nothing
was sent anywhere. People who run Prometheus, Grafana or a syslog server on
their NAS want Hikidashi in them.

## Decision

**Off unless asked for, and nothing a library holds in either.**

- **Metrics are pulled.** `HIKIDASHI_METRICS=on` serves `/metrics` in the
  Prometheus format; the server never pushes them anywhere.
  `prometheus_client` is imported only then.
- **A token of its own, not a #103 scope.** `HIKIDASHI_METRICS_TOKEN` is
  what Prometheus's `authorization` setting sends, set in one place, with no
  account behind it and no 30-day expiry to rotate. Without one, private
  addresses only. A session cookie never counts: these are the server's
  numbers, not a person's.
- **One scrape covers every container.** The workers' state is already in
  the database, so the server reads it there, through one collector cached
  for `HIKIDASHI_METRICS_REFRESH` seconds, rather than each worker exposing a
  port. Stage completions are therefore a gauge of reels done at the
  current version, not a counter.
- **Bounded labels, no personal data.** Route templates, stages, states,
  worker and provider names. Never a shortcode, URL, caption, query, username
  or token. A user's hosted API is `api="user"`, summed, unless an admin asks
  for per-user labels.
- **Logs are sent only where configured**, to syslog (UDP, TCP, a unix
  socket) or Loki, with the stdlib, from a queue that drops the oldest lines
  rather than block or grow while the receiver is away. Every line is
  redacted first. The file and stdout stay as they are.

## Consequences

- Two new configured-only destinations in "what leaves the machine": a
  syslog server and Loki, with log lines.
- `/healthz` and `/readyz` are new unauthenticated routes. They say only
  whether the process is up and the database is ready.
- syslog over TLS is not built; a local rsyslog can forward over TLS.
- Settings are environment only: there is no switch on the admin pages or in
  the CLI yet.
