# 0013. Standard tokens: user-scoped API tokens, admin-only worker tokens

- **Status:** Accepted. Supersedes [0012](0012-nas-installs-templates-and-yaml.md)'s
  note that `HIKIDASHI_WORKER_TOKEN` covers a headless remote worker.
- **Date:** 2026-10-29
- **Issue:** #103; [architecture.md](../architecture.md#security-boundaries),
  [operation.md](../operation.md#from-a-script-an-api-token)

## Context

Only a browser could read a library. Every route but `/api/worker/*` went
through the session cookie or the proxy's header, so a script, a monitor or an
assistant checking a server (the case that raised #103: a fresh Unraid install,
checked from another machine) had to sign in through a browser and lift the
cookie. Worker tokens existed, but only for `/api/worker/*`, and beside them
the server also took `HIKIDASHI_WORKER_TOKEN` from its own environment: a
shared secret with no owner, revoked only by a restart.

We checked the plan against GitHub (fine-grained personal access tokens and
their token format), GitLab (personal access tokens and runner tokens), Gitea
(scopes), Immich (API keys), Buildkite (agent tokens), RFC 6750 and OWASP's
REST cheat sheet.

## Decision

**The browser keeps its cookie; everything else sends a bearer token.** The
SPA's `<img>`, `<video>` and download links cannot send a header, and an
HttpOnly cookie is safer from script than a token kept in JavaScript. A
request with neither a session nor a token is a 401 with `WWW-Authenticate:
Bearer`.

**One token system, two kinds, in one table** (`api_token`, with `kind`).
Both are random, shown once and kept as a SHA-256, record their last use, are
revocable, and go with their owner's account. Both read as GitHub's do:
`hk_api_` or `hk_wkr_`, 30 base62 characters, and a CRC32 check, so a leaked
one is recognisable and a mistyped one is refused without a lookup.

- **API tokens** are any person's, for their own library, with the scopes
  they pick: `read:library`, `write:library`, and for admins `read:admin` and
  `write:admin`. `read` is `GET` and `HEAD`; `write` is the rest and includes
  `read` (Gitea's model). Admin scopes count only while the owner is an
  admin, checked on every request. They live 30 days at most and are never
  extended. A token cannot change a password, sign out sessions, or make or
  revoke tokens.
- **Worker tokens** are admins' only, open `/api/worker/*` and nothing else,
  and last until revoked, as GitLab runner and Buildkite agent tokens do: a
  remote worker runs unattended. They work only while their maker is an admin.

**Neither kind stands in for the other.** An admin's API token is refused at
`/api/worker/*`, and a worker token everywhere else.

**The server no longer takes `HIKIDASHI_WORKER_TOKEN`.** It is what a remote
worker sends. Set on the server, it is ignored, with a warning at start-up.

**Errors are RFC 6750's:** 401 `invalid_token`, 403 `insufficient_scope`
naming the scope. A token is never read from the URL.

## Alternatives

- **Bearer tokens for the browser too.** Media and download links would need
  signed URLs or a cookie anyway, and the token would sit where any injected
  script could read it.
- **Read-only tokens only**, the issue's first plan, with write scopes later.
  The user picks the scopes instead, as on GitHub, GitLab and Immich; reading
  stays the default.
- **Any user may make a worker token.** The job queue is the whole server's:
  a worker downloads anyone's media and writes transcripts and tags everyone
  sees. GitLab and Buildkite keep runners and agents to owners and admins,
  and their runners see only their own projects' jobs; ours would see
  everyone's.
- **Admin tokens that also work as worker tokens.** One leaked token would
  then open both the API and the queue. GitLab keeps runner tokens and
  personal access tokens apart for the same reason, and advises against
  personal tokens in CI.
- **Worker tokens that expire in 30 days.** A remote worker would stop each
  month until someone noticed.
- **Keeping the environment secret as a fallback.** Nothing would break, but
  it would stay the one credential with no owner, no listing and no revoking.
- **Two tables, one per kind.** The same columns, the same checks and the
  same listing twice; `kind` keeps them apart at one lookup.

## Consequences

- A script reads a library with `curl -H "Authorization: Bearer hk_api_…"`,
  and #54's MCP server has the per-user token it needs.
- A server whose remote worker relied on `HIKIDASHI_WORKER_TOKEN` in the
  server's environment refuses that worker after the upgrade, until an admin
  makes it a worker token. Worker tokens made on the Pipeline page before
  0.15.0 move into `api_token` and keep working.
- Demoting an admin stops their admin scopes and their worker tokens at
  once; disabling an account revokes all its tokens.
- Behind a signing-in proxy, a script with a token reaches the app with no
  user header; a user header the proxy sends wins over the token.
