# 0005. Local accounts, no Instagram login

- **Status:** Accepted
- **Date:** 2026-10-23
- **Issue:** #12

## Context

More than one person using one server (#12) needs to know who is asking. The
obvious choice was "sign in with Instagram", for identity only (who you are and
your @handle) with no data access.

## Decision

Hikidashi has local accounts: a username and an argon2 password. There is no
sign-up; an admin invites people with a one-time link. `HIKIDASHI_AUTH=proxy`
lets a proxy in front (Authelia, Tailscale, Cloudflare Access) sign people in
instead, and `none` keeps the single-user mode. A person's Instagram handle is
self-declared and never verified.

## Alternatives

- **Instagram login.** Not available without Meta's approval. The Basic
  Display API, the only login that worked for personal accounts, was shut down
  in December 2024. Instagram API with Instagram Login works only for Business
  and Creator accounts, and outside development mode (hand-added testers) it
  needs App Review, which this project isn't going through.
- **Open sign-up.** A self-hosted library of your saves has no reason to let
  strangers in.

## Consequences

- Sessions and invites are random tokens stored as SHA-256, so a copy of the
  database can't be used to sign in
  ([architecture.md](../architecture.md#security-boundaries)).
- Libraries are separated by `user_item`, not by account system: one code path
  for all three modes, with user 1 as the owner under `none`.
- Each person brings their own Instagram export. Nothing about their account is
  fetched with their credentials, and yt-dlp fetches public posts without
  cookies.
