# 0023. Logging and monitoring settings on the Account page

- **Status:** Accepted
- **Date:** 2026-11-14
- **Issue:** none (asked for directly); follows #149

## Context

[ADR 0022](0022-metrics-and-log-shipping.md) made metrics and log shipping
environment variables only. On a NAS that means editing a template and
recreating the container to try a syslog address or switch metrics on, and
an admin cannot see from the app what is on.

## Decision

**The Account page can set them; the environment still wins.**

- **Saved in the database.** The settings are the same variables, saved as
  the server's `monitoring` row in the `setting` table (JSON, by variable
  name). A variable that is set, or its `_FILE`, wins, and the page shows it
  locked with its name. So a deployment that pins them in Compose keeps them
  pinned.
- **Secrets are write-only.** The metrics token and Loki's token and password
  are sealed with the server's secret key, as hosted AI keys are
  (`app/ai/secrets.py`), and only whether one is saved is ever sent back.
- **Applied without a restart.** The server rebuilds its log handlers when a
  change is saved. `/metrics` and its middleware are always routed now and
  answer 404 while metrics are off; switching on builds the registry then, so
  `prometheus_client` is still imported only when on. A change starts the
  counters again, which Prometheus reads as a restart.
- **Workers beside the server follow; workers elsewhere do not.** A worker
  that shares the database looks for a change every 30 seconds. A worker on
  another machine has no database, and the server does not send it Loki
  credentials: it keeps to its own environment.
- **Checked before saved.** A change is saved only if the whole result would
  install (a syslog URL's scheme, a port in range, an allowed network that
  parses). A port already taken is only found when applied; the page says
  so, and the rest is on.

## Consequences

- Admins can turn metrics and shipping on and off, and see their state, from
  the app.
- Saved secrets travel with a database backup, sealed; whoever has all of
  `data/` (with `secret.key`) can read them, as with hosted AI keys.
- A remote worker's shipping is still set on that machine.
- There is still no CLI switch, and syslog over TLS is still not built.
