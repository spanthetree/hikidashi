# 0012. NAS installs: Unraid templates and TrueNAS YAML, any user, no PUID

- **Status:** Accepted. Its note on `HIKIDASHI_WORKER_TOKEN` is
  **Superseded by** [0013](0013-standard-api-and-worker-tokens.md): the
  server no longer takes it.
- **Date:** 2026-10-28
- **Issue:** #87; [install-unraid.md](../install-unraid.md), [install-truenas.md](../install-truenas.md)

## Context

Unraid and TrueNAS SCALE are where most people would self-host Hikidashi, and
neither of them runs a checkout. Both install containers from published images
(#77), through their own UI. Hikidashi is three or four containers that share
one folder: `app` and `server-worker` from the server image, `worker` from
the worker image, and Ollama. Before #87 the images ran as root, so whatever
they wrote to appdata ended up root-owned. Both platforms expect a
configurable user instead: Unraid's `nobody:users` (99:100), and TrueNAS's
`apps` (568:568).

The state of each platform, as of October 2026:

- **Unraid 7.2** has no built-in Compose, and its roadmap lists it with no
  date. Compose comes from a Community Applications plugin, which has forked
  ("Compose Manager" and "Compose Manager Plus"), and guides disagree on which
  one is maintained. Community Applications (CA) installs one container per
  XML template. A submission needs a public repo with an OSI license, valid
  templates and a `ca_profile.xml`. A forum support thread is no longer
  required, and recent submissions have been approved automatically. CA is
  also adding compose packages (`AppTemplateType` `compose`), aimed at
  Unraid 8, which isn't out yet.
- **TrueNAS SCALE 24.10 and later** (25.10 "Goldeye" now) runs apps on Docker
  Compose. *Install via YAML* takes a compose file pasted into the UI, with
  host paths, `user:`, healthchecks and the standard GPU reservation. The
  `truenas/apps` catalog takes apps in its own layout (`app.yaml`,
  `questions.yaml`, templates, CI) through review.

## Decision

- **Unraid: CA templates first**, one per container, in `deploy/unraid/`:
  `hikidashi` (the server), `hikidashi-server-worker`, `hikidashi-worker` and
  `hikidashi-worker-cuda`. Ollama uses the Ollama template already in CA.
  Until the listing goes through, the XML installs by hand. **Compose
  Manager** is documented as the alternative, running our own compose files
  with `deploy/unraid/compose.env`.
- **TrueNAS SCALE: Install via YAML**, one compose file in `deploy/truenas/`,
  with published images, host paths on a dataset, `user: "568:568"`, and the
  GPU block commented out on the worker. **No catalog app** unless people ask
  for one.
- **Any user, no PUID/PGID.** The images run as whatever user they are
  started as: `--user 99:100` in the Unraid templates, `user: "568:568"` in
  the TrueNAS file. The only change needed was `HOME=/tmp`, because a user
  with no home can't write to `/`. There is no entrypoint that switches user
  or `chown`s `/data` on start. A library first started as root needs one
  `chown -R`, and the install docs give it.
  The compose files take the user from `HIKIDASHI_USER`, root unless set, so
  a Compose Manager install can run as 99:100 too, and existing installs
  don't change.
- **The containers share `/data` and nothing else.** They meet in the SQLite
  file, its locks, and `run/worker.wake`, so a template per container needs
  no shared network and no start order. The one rule is that every template
  mounts the **same host path** at `/data`. Started together on a new
  library, they used to race to create the database, and the server-worker
  crashed. Opening the database now takes turns, with an flock on
  `hikidashi.sqlite3-init.lock`.
- **The templates restart on failure** (`--restart=unless-stopped`), as the
  compose files do. Unraid's own autostart only starts containers at boot.
- **The health probe is `/api/auth`**, which answers without signing in. Not
  `/api/stats`, which needs a session once `HIKIDASHI_AUTH=local`.
- **No pre-seeded admin.** The setup link is in the server's log, which both
  platforms show in their UI. `HIKIDASHI_WORKER_TOKEN` already covers a
  remote worker that has to start headless.
- **Versions:** the Unraid templates use `:latest`, as CA templates do, and
  the tag can be edited. The TrueNAS file pins the release, so that the
  server and the worker stay on the same one. The pin is bumped with every
  version, like the other files in CONTRIBUTING.md.

## Alternatives

- **Compose Manager first on Unraid.** It is closest to what exists, and the
  whole stack stays in one place. But it needs a third-party plugin whose
  maintained fork isn't settled, and it isn't how most Unraid users install
  apps. It is kept as the documented second option.
- **The `truenas/apps` catalog.** It gives the best experience on SCALE: a
  form, upgrades in the UI. It costs its own app layout, CI and a review
  for every release, which is a lot of upkeep for a project this size. Not
  now.
- **CA compose packages.** They would give one install for the whole stack
  on Unraid, as on TrueNAS. They are aimed at Unraid 8, so they are worth
  revisiting once it ships.
- **The TrueNAS Custom App form.** Every field is typed by hand, once per
  container, four times over. It is only mentioned as a fallback.
- **PUID/PGID with an entrypoint** (the linuxserver.io convention). Unraid
  users know it, and it can `chown` an old root-owned library on start. But
  it needs a root entrypoint, `gosu` or `su-exec` in both images, and a
  recursive `chown` over thousands of media files on every start. That buys
  nothing that `--user` doesn't already do, and TrueNAS uses `user:` anyway.
- **A `HEALTHCHECK` in the server image.** `server-worker` runs from the
  same image and serves no HTTP, so it would show as unhealthy unless every
  layout turned the check off for it. The probe goes in the app's template
  and compose file instead.

## Consequences

- Nothing installs until the images are on GHCR (#77). The templates and the
  YAML name `ghcr.io/spanthetree/hikidashi-server`, `-worker` and
  `-worker-cuda`.
- A CA listing needs a `LICENSE` at the root of the submitted repo, which
  Hikidashi doesn't have yet, and `ca_profile.xml` at that root too. It lives
  in `deploy/unraid/` until then, and goes either to the root of this repo
  or to a small templates repo of its own when the templates are submitted.
- Four Unraid templates, not one: the server-side worker is the server image
  with `hikidashi worker` in Post Arguments. Leaving it out is the easiest
  mistake to make, as reels are then imported and never fetched. The docs say
  so first.
- Upgrades are by hand on both platforms: on Unraid, *Check for updates* on
  each container; on TrueNAS, edit the tags in the YAML. The schema migrates
  on start either way.
- The install files are checked, not installed, by `tests/test_deploy.py`:
  well-formed XML, the same `/data` on every template, and the TrueNAS pin
  matching `pyproject.toml`. A real install on each platform is a hand
  check.
