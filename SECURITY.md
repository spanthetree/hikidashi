# Security policy

## Supported versions

Security fixes go into the latest release only. Hikidashi follows
[Semantic Versioning](https://semver.org), and each release says in the
[changelog](CHANGELOG.md) if anything about upgrading needs care, so staying
on the latest image (`:latest`, or the newest `0.x` tag) is the supported path.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub:
go to the repository's **Security** tab and choose **Report a vulnerability**
([direct link](https://github.com/spanthetree/hikidashi/security/advisories/new)).

Include what you can of:

- the version (the image tag, or the release you installed) and how it is installed
  (Docker Compose, Unraid, TrueNAS, uv);
- the `HIKIDASHI_AUTH` mode (`none`, `local` or `proxy`) and whether a reverse
  proxy is in front;
- the steps to reproduce it, and what an attacker gains.

You should get a reply within a week. Once a fix is released, the advisory is
published with credit to you, unless you'd rather not be named.

## What counts

Hikidashi is a self-hosted app for a household or a small group, usually on a
home network. [docs/architecture.md](docs/architecture.md#security-boundaries)
describes the boundaries it is meant to keep. A way across one of them is a
vulnerability, for example:

- one user seeing, or learning of the existence of, a reel in another user's
  library, including through `/media`, search or exports;
- signing in, or acting as someone, without their password, session, API token
  or a trusted proxy's say-so;
- a request from another website changing anything on the server (CSRF);
- an API token doing more than its scopes allow;
- a remote worker, with a valid worker token, doing more than recording its
  stage's results for the reels it has leased;
- reading or writing files outside the data folder, or running commands, from
  an import ZIP, a caption or anything else a user or Instagram supplies.

Not vulnerabilities on their own:

- `HIKIDASHI_AUTH=none` letting anyone who can reach the server use it. That
  is what the mode is for; it is meant for a single person on a trusted network.
- Plain HTTP on a local network. Put Hikidashi behind a reverse proxy with TLS
  if it is reachable from anywhere else.
- Issues in a model, Ollama, FFmpeg, yt-dlp or another
  [third-party component](THIRD_PARTY.md), unless Hikidashi's own use of it
  makes them exploitable. Report those upstream.
