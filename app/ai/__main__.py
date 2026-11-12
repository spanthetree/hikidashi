"""CLI: hikidashi ai <command>

Hosted AI from the command line: everything the settings pages do (#125).

On the server's host (or `docker compose exec app hikidashi ai …`) it opens
the database, as `hikidashi token` does: `--user NAME` acts for that user (the
first admin by default), `--server-api` on the server's API. From another
machine, `--server https://…` goes through the same routes as the web pages,
with an API token (scopes read:ai and write:ai) from HIKIDASHI_API_TOKEN or a
prompt, never a flag; a worker token cannot.

A key is read from a hidden prompt or, with --stdin, from standard input;
never from an argument, and no command prints one.

Exit codes: 0 OK; 1 failed (Test connection rejected the key, say); 2 usage;
3 not allowed (not an admin, a missing scope, a setting locked by env, stages
not all set, no budget).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.providers.presets import PRESETS, STAGES

OK, FAILED, USAGE, NOT_ALLOWED = 0, 1, 2, 3


class Failed(Exception):
    def __init__(self, message: str, code: int = FAILED):
        super().__init__(message)
        self.code = code


# --- backends: the database here, or a server's routes ------------------------


class Local:
    """The service functions, on this host's database."""

    def __init__(self, user: str | None, server_api: bool):
        from app.accounts import by_name, first_admin
        from app.config import settings
        from app.db import open_db

        settings.ensure_dirs()
        self.conn = open_db(settings.db_path)
        self.data_dir = settings.db_path.parent
        actor = by_name(self.conn, user) if user else first_admin(self.conn)
        if actor is None:
            raise Failed(
                f"there is no account called {user}" if user else "there is no admin yet",
                USAGE,
            )
        if server_api and not actor.is_admin:
            raise Failed(f"{actor.username} is not an admin", NOT_ALLOWED)
        self.actor = actor
        self.owner: int | None = None if server_api else actor.id

    def __call__(self, op: str, **kw: Any) -> Any:  # noqa: C901 -- one case an op
        from app.ai import service, status, usage

        c, d, a, o = self.conn, self.data_dir, self.actor, self.owner
        try:
            match op:
                case "show":
                    return service.page(c, o, d)
                case "set":
                    service.set_stage(c, a, o, kw["stage"], d, **kw["fields"])
                    return service.page(c, o, d)
                case "key_set":
                    service.set_key(c, a, o, kw["provider"], kw["key"], d)
                    return service.page(c, o, d)
                case "key_remove":
                    service.remove_key(c, a, o, kw["provider"], d)
                    return service.page(c, o, d)
                case "budget":
                    service.set_budget(c, a, o, kw["usd"], d)
                    return service.page(c, o, d)
                case "language":
                    service.set_language(c, a, o, kw["code"], d)
                    return service.page(c, o, d)
                case "use":
                    service.set_choice(c, a, o, kw["use"], d)
                    return service.page(c, o, d)
                case "models":
                    return service.models(c, o, kw["provider"], d)
                case "test":
                    return service.test(c, o, d, kw.get("stage"))
                case "backlog":
                    return service.backlog(c, o, d, actor=a.id, scope=kw.get("scope"))
                case "on":
                    service.turn_on(c, a, o, d, scope=kw.get("scope"), backlog=kw["backlog"])
                    return service.page(c, o, d)
                case "off":
                    service.off_by(c, a, o, d)
                    return service.page(c, o, d)
                case "status":
                    if kw.get("all"):
                        return status.apis(c, d)
                    return [status.one(c, o, d)]
                case "usage":
                    api = None if kw.get("all") else service.load(c, o, d).name
                    return usage.report(c, api=api, month=kw.get("month"))
        except service.AiError as exc:
            raise Failed(str(exc), exc.exit) from exc
        except ValueError as exc:
            raise Failed(str(exc), USAGE) from exc
        raise AssertionError(op)


class Remote:
    """The same, through a server's routes, with an API token."""

    def __init__(self, server: str, token: str, server_api: bool):
        self.server = server.rstrip("/")
        self.token = token
        self.server_api = server_api
        self.base = "/api/admin/settings/providers" if server_api else "/api/me/providers"

    def _send(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.server + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "hikidashi-ai",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("detail")
            except (ValueError, AttributeError):
                detail = None
            code = (
                NOT_ALLOWED
                if exc.code in (401, 403)
                else USAGE
                if exc.code in (400, 404, 409, 422)
                else FAILED
            )
            raise Failed(f"{exc.code}: {detail or exc.reason}", code) from None
        except (urllib.error.URLError, OSError) as exc:
            raise Failed(f"cannot reach {self.server}: {getattr(exc, 'reason', exc)}") from None

    def __call__(self, op: str, **kw: Any) -> Any:  # noqa: C901 -- one case an op
        b = self.base
        q = urllib.parse.urlencode
        match op:
            case "show":
                return self._send("GET", b)
            case "set":
                return self._send("PUT", f"{b}/stages/{kw['stage']}", kw["fields"])
            case "key_set":
                return self._send("PUT", f"{b}/keys/{kw['provider']}", {"key": kw["key"]})
            case "key_remove":
                return self._send("DELETE", f"{b}/keys/{kw['provider']}")
            case "budget":
                return self._send("PUT", f"{b}/budget", {"usd": kw["usd"]})
            case "language":
                return self._send("PUT", f"{b}/language", {"code": kw["code"]})
            case "use":
                return self._send("PUT", f"{b}/use", {"use": kw["use"]})
            case "models":
                return self._send("GET", f"{b}/models?{q({'provider': kw['provider']})}")
            case "test":
                return self._send("POST", f"{b}/test", {"stage": kw.get("stage")})
            case "backlog":
                scope = kw.get("scope")
                return self._send(
                    "GET", f"{b}/backlog" + (f"?{q({'scope': scope})}" if scope else "")
                )
            case "on":
                return self._send(
                    "POST",
                    f"{b}/switch",
                    {"on": True, "scope": kw.get("scope"), "backlog": kw["backlog"]},
                )
            case "off":
                return self._send("POST", f"{b}/switch", {"on": False})
            case "status":
                if kw.get("all"):
                    return self._send("GET", "/api/pipeline")["apis"]
                page = self._send("GET", b)
                return [
                    {
                        "name": "api:server" if self.server_api else "yours",
                        **page["switch"],
                        "spent": page["spent"],
                        "budget": page["budget"]["value"],
                    }
                ]
            case "usage":
                month = kw.get("month")
                path = "/api/admin/ai/usage" if kw.get("all") else f"{b}/usage"
                return self._send("GET", path + (f"?{q({'month': month})}" if month else ""))
        raise AssertionError(op)


# --- printing ---------------------------------------------------------------


WAYS = {"mine": "your API", "server": "the server's API", "workers": "workers"}


def _source(v: dict[str, Any]) -> str:
    s = v.get("source", "unset")
    return "" if s in ("saved", "unset") else f"  ({s.replace('env:', 'set by environment: ')})"


def print_page(p: dict[str, Any]) -> None:
    sw = p["switch"]
    state = "on" + (f" for {sw['scope']}" if sw.get("scope") else "") if sw["on"] else "off"
    if not sw["on"] and sw.get("reason"):
        state += f": {sw['reason']}"
    print(f"  switch      {state}")
    if "applies" in p:
        way = WAYS.get(p["applies"], p["applies"])
        print(f"  your reels  {way}  (choice: {p.get('use')})")
    for name, st in p["stages"].items():
        prov = st["provider"]["value"] or "-"
        model = st["model"]["value"] or (
            f"{st['default_model']} (default)" if st.get("default_model") else "-"
        )
        url = f"  {st['base_url']['value']}" if st["base_url"]["value"] else ""
        print(f"  {name:<10}  {prov} · {model}{url}{_source(st['provider'])}{_source(st['model'])}")
    keys = [f"{k} {v['masked']}{_source(v)}" for k, v in p["keys"].items() if v["set"]]
    keys += [f"{k}: {v['problem']}" for k, v in p["keys"].items() if v.get("problem")]
    print(f"  keys        {', '.join(keys) or 'none'}")
    budget = p["budget"]["value"]
    print(
        f"  budget      {f'{budget:g} USD a month' if budget else 'none'}"
        f"{_source(p['budget'])} · spent {p.get('spent', 0):.2f} this month"
    )
    lang = p["language"]["value"]
    print(f"  language    {lang or 'detected'}{_source(p['language'])}")
    if p.get("problems"):
        print("  to turn on  " + "\n              ".join(p["problems"]))


def print_status(rows: list[dict[str, Any]]) -> None:
    for a in rows:
        if a.get("on") is None:
            print(f"  {a['name']}  reported {a.get('spent', 0):.2f} USD this month")
            continue
        state = ("on" + (f" for {a['scope']}" if a.get("scope") else "")) if a["on"] else "off"
        if not a["on"] and a.get("reason"):
            state += f" ({a['reason']})"
        extra = ""
        if "in_flight" in a:
            extra = f" · {a['in_flight']} in flight · {a['waiting']} waiting"
        budget = f" of {a['budget']:g}" if a.get("budget") else ""
        print(f"  {a['name']}  {state}{extra} · {a.get('spent', 0):.2f}{budget} USD this month")


def print_usage(r: dict[str, Any]) -> None:
    print(
        f"  {r['month']}  {r['total_usd']:.4f} USD"
        f" (estimated; prices checked {r['prices_checked']})"
    )
    for row in r["rows"]:
        what = (
            f"{row['audio_seconds'] / 60:,.1f} min"
            if row["audio_seconds"]
            else f"{row['input_tokens']:,} in / {row['output_tokens']:,} out tokens"
        )
        print(
            f"    {row['api']:<16} {row['stage']:<7} {row['model']:<40}"
            f" {row['reels']:>5} reels  {what}  {row['usd']:.4f}"
        )
    for u in r.get("server_by_user", []):
        print(f"    server's API for {u['username']}: {u['usd']:.4f}")
    if r.get("unpriced"):
        print(f"  no price known for {', '.join(r['unpriced'])}: counted as 0")


# --- commands ---------------------------------------------------------------


def _key(stdin: bool) -> str:
    if stdin:
        return sys.stdin.readline().strip()
    if not sys.stdin.isatty():
        raise Failed("no terminal to ask for the key on: pipe it in with --stdin", USAGE)
    return getpass.getpass("  key (not shown): ").strip()


def _on(call: Any, args: argparse.Namespace) -> Any:
    b = call("backlog", scope=args.for_)
    low, high = b.get("usd_low", 0), b.get("usd_high", 0)
    cost = f"about {low:.2f} USD" if abs(high - low) < 0.005 else f"about {low:.2f}–{high:.2f} USD"
    print(
        f"  {b['reels']} reels waiting, {cost}"
        + (f" (no price for {', '.join(b['unpriced'])})" if b.get("unpriced") else "")
    )
    backlog = True if args.backlog else False if args.new_only else None
    if backlog is None and b["reels"]:
        if args.yes or not sys.stdin.isatty():
            raise Failed(
                "say --backlog to process these too, or --new-only to leave them to workers", USAGE
            )
        answer = input("  process these too? [y]es / [n]ew reels only / [c]ancel: ").strip().lower()
        if answer.startswith("c") or not answer:
            raise Failed("cancelled", OK)
        backlog = answer.startswith("y")
    elif not args.yes and sys.stdin.isatty():
        if input("  turn it on? [y/N] ").strip().lower() != "y":
            raise Failed("cancelled", OK)
    return call("on", scope=args.for_, backlog=bool(backlog) if backlog is not None else True)


def _set(args: argparse.Namespace, call: Any) -> Any:
    fields = {"provider": args.provider, "model": args.model, "base_url": args.base_url}
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        raise Failed("give --provider, --model or --base-url", USAGE)
    return call("set", stage=args.stage, fields=fields)


def _key_cmd(args: argparse.Namespace, call: Any) -> Any:
    if args.key_command == "set":
        return call("key_set", provider=args.provider, key=_key(args.stdin))
    return call("key_remove", provider=args.provider)


def _budget(args: argparse.Namespace, call: Any) -> Any:
    try:
        usd = None if args.usd in ("none", "0") else float(args.usd)
    except ValueError:
        raise Failed("a budget is a number of US dollars a month, or none", USAGE) from None
    return call("budget", usd=usd)


def _switch_on(args: argparse.Namespace, call: Any) -> Any:
    return _on(call, args)


COMMANDS: dict[str, Any] = {
    "show": lambda a, call: call("show"),
    "set": _set,
    "key": _key_cmd,
    "budget": _budget,
    "language": lambda a, call: call("language", code=None if a.code == "detect" else a.code),
    "use": lambda a, call: call("use", use=a.choice),
    "models": lambda a, call: call("models", provider=a.provider),
    "test": lambda a, call: call("test", stage=a.stage),
    "on": _switch_on,
    "off": lambda a, call: call("off"),
    "status": lambda a, call: call("status", all=a.all),
    "usage": lambda a, call: call("usage", all=a.all, month=a.month),
}


def run(args: argparse.Namespace, call: Any) -> int:
    out = COMMANDS[args.command](args, call)
    return show(args, out)


def show(args: argparse.Namespace, out: Any) -> int:
    """Print a command's result. Test connection's exit says whether it passed."""
    c = args.command
    passed = all(r["ok"] for r in out.values()) if c == "test" else True
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    elif c == "models":
        print("\n".join(f"  {m}" for m in out) or "  none listed")
    elif c == "test":
        for stage, r in out.items():
            model = f" ({r['model']})" if r.get("model") else ""
            detail = f": {r['detail']}" if r.get("detail") and not r["ok"] else ""
            print(f"  {stage:<8} {r['said']}{model}{detail}")
    elif c == "status":
        print_status(out)
    elif c == "usage":
        print_usage(out)
    else:
        print_page(out)
    return OK if passed else FAILED


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hikidashi ai", description=__doc__.splitlines()[2])
    who = argparse.ArgumentParser(add_help=False)
    g = who.add_mutually_exclusive_group()
    g.add_argument(
        "--user", metavar="NAME", help="act for this user, on the host (default: the first admin)"
    )
    g.add_argument("--server-api", action="store_true", help="the server's API, not a user's")
    who.add_argument(
        "--server",
        metavar="URL",
        default=os.environ.get("HIKIDASHI_SERVER"),
        help="a server elsewhere (default HIKIDASHI_SERVER), with an API token from "
        "HIKIDASHI_API_TOKEN or a prompt",
    )
    who.add_argument("--json", action="store_true", help="print JSON")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "show",
        parents=[who],
        help="each stage's provider and model, and where each comes from; keys masked",
    )
    s = sub.add_parser("set", parents=[who], help="a stage's provider, model or base URL")
    s.add_argument("stage", choices=[*STAGES, "all"])
    s.add_argument("--provider", choices=list(PRESETS))
    s.add_argument("--model", help='a model ID; "" for the preset\'s default')
    s.add_argument("--base-url", help="for an OpenAI-compatible URL")
    k = sub.add_parser("key", help="set or remove a provider's key")
    ks = k.add_subparsers(dest="key_command", required=True)
    kset = ks.add_parser(
        "set", parents=[who], help="from a hidden prompt, or --stdin; never an argument"
    )
    kset.add_argument("provider", choices=list(PRESETS))
    kset.add_argument("--stdin", action="store_true", help="read the key from standard input")
    ks.add_parser("remove", parents=[who]).add_argument("provider", choices=list(PRESETS))
    sub.add_parser("budget", parents=[who], help="the monthly budget in USD, or none").add_argument(
        "usd"
    )
    sub.add_parser(
        "language", parents=[who], help="force a transcription language (en, ja, …), or detect"
    ).add_argument("code")
    sub.add_parser(
        "use",
        parents=[who],
        help="the one choice: default (follow the system default), mine (hosted AI) or "
        "workers; with --server-api, the system default itself: mine or workers",
    ).add_argument("choice", choices=["default", "mine", "workers"])
    sub.add_parser(
        "models", parents=[who], help="the models a provider offers this key"
    ).add_argument("provider", choices=list(PRESETS))
    sub.add_parser(
        "test", parents=[who], help="Test connection: one tiny request per stage"
    ).add_argument("stage", nargs="?", choices=STAGES)
    on = sub.add_parser(
        "on", parents=[who], help="switch on: shows the reels waiting and the estimate, then asks"
    )
    on.add_argument(
        "--for",
        dest="for_",
        choices=["me", "all"],
        help="with --server-api: who it is for (default all: the system default)",
    )
    b = on.add_mutually_exclusive_group()
    b.add_argument("--backlog", action="store_true", help="process the reels waiting too")
    b.add_argument("--new-only", action="store_true", help="leave the reels waiting to workers")
    on.add_argument("--yes", action="store_true", help="do not ask")
    sub.add_parser(
        "off",
        parents=[who],
        help="switch off: in-flight requests finish, the rest go back to workers",
    )
    st = sub.add_parser(
        "status", parents=[who], help="on or off (and why), waiting, in flight, this month's spend"
    )
    st.add_argument("--all", action="store_true", help="every API (admins)")
    us = sub.add_parser("usage", parents=[who], help="a month's use, per stage and model")
    us.add_argument("--all", action="store_true", help="every API (admins)")
    us.add_argument("--month", help="YYYY-MM (default: this one)")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        if args.server:
            if args.user:
                raise Failed(
                    "--user is for the host; with --server, the token says who you are", USAGE
                )
            token = os.environ.get("HIKIDASHI_API_TOKEN") or (
                getpass.getpass("  API token (not shown): ").strip() if sys.stdin.isatty() else ""
            )
            if not token:
                raise Failed(
                    "set HIKIDASHI_API_TOKEN to an API token with read:ai and write:ai", USAGE
                )
            call: Any = Remote(args.server, token, args.server_api)
        else:
            call = Local(args.user, args.server_api)
        return run(args, call)
    except Failed as exc:
        if exc.code != OK:
            print(f"  {exc}", file=sys.stderr)
        else:
            print(f"  {exc}")
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
