# Accounts and access

Sharing one server between several people, signing in, and reaching it from
scripts. [Back to the README](../README.md)

- [Modes](#modes)
- [Accounts on the server](#accounts-on-the-server)
- [Behind a sign-in proxy](#behind-a-sign-in-proxy)
- [API tokens](#api-tokens)
- [HTTPS](#https)

## Modes

`HIKIDASHI_AUTH` decides who a request comes from:

| Mode | Who is signed in |
|---|---|
| `none` (default) | Nobody signs in: whoever can reach the server is the library's owner. For one person on a trusted network |
| `local` | Accounts with passwords, a library each |
| `proxy` | The user your sign-in proxy (Authelia, Tailscale, Cloudflare Access…) names |

With accounts, everyone sees only their own saves. A reel two people saved is
downloaded, transcribed and tagged once, but their tags, collections and
recipe edits stay their own. [Security boundaries](architecture.md#security-boundaries)
describes how this is enforced.

## Accounts on the server

Set `HIKIDASHI_AUTH=local` and restart. Everything already imported stays
yours.

1. **Claim the library.** Run `./hikidashi user passwd local`, pick a
   password, and sign in as `local` (`./hikidashi user rename local <name>`
   picks another name). Or open the `/setup?token=…` link the server writes to
   its log at startup while nobody can sign in.
2. **Invite people.** There is no open sign-up. On **Your account**, make a
   one-time link and send it, or run
   `./hikidashi user invite --url https://your.host`. Each link makes one
   account and lasts 7 days.
3. **Everyone imports their own export**, from the web app or with
   `./hikidashi import --user <name> export.zip`.

Signing in with Instagram isn't offered: it needs Meta's App Review, and the
API that served personal accounts was shut down in December 2024. The
@handle on an account is whatever its owner enters, and isn't checked.

## Behind a sign-in proxy

If a proxy already signs people in, set `HIKIDASHI_AUTH=proxy`. Hikidashi
takes the user from the proxy's `Remote-User` header
(`HIKIDASHI_PROXY_HEADER`), and believes it only from the addresses in
`HIKIDASHI_TRUSTED_PROXIES` (default: this machine). Make sure nothing but the
proxy can reach the port. A name with no account is refused unless
`HIKIDASHI_PROXY_CREATES=1`.

## API tokens

Scripts, monitors and assistants sign in with an API token. Make one on
**Your account** under **API tokens**: name it, tick what it may do, and pick
how long it lasts, up to 30 days. It is shown once.

```sh
curl -H "Authorization: Bearer hk_api_…" https://your.host/api/stats
```

A token acts as you and reads only your library: `read:library` reads it, and
`write:library` may change it too. Admins can also grant `read:admin` or
`write:admin`, for the pipeline, logs and accounts. A token can't change a
password, make another token, or be extended. Admins see everyone's tokens on
the **Pipeline** page and can revoke any.

From the server's command line:

```sh
./hikidashi token make monitor --user alice --scope read:library --days 7
./hikidashi token list
./hikidashi token revoke 3
```

Worker tokens, for [remote workers](remote-workers.md), are a separate kind:
only admins make them, they open the worker routes and nothing else, and they
last until revoked.

## HTTPS

Before the server is reachable from outside your network, put it behind a
reverse proxy with HTTPS and set `HIKIDASHI_ORIGIN` to its public address
(`https://reels.example.com`). That also marks the sign-in cookie Secure.

On your own network, plain HTTP (`http://192.168.1.20:8080`) works, with two
limits, because browsers keep some features to HTTPS pages: the map's **Near
me** can't find you, and the copy buttons fall back to an older way of
copying. If the browser refuses that too, the button says so and selects the
text for you to copy.
