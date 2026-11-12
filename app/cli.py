"""CLI: hikidashi <command> [flags]

One entry point for the pipeline. Each command hands its flags, unchanged, to
the `python -m app.<module>` it stands for, so `hikidashi tag --help` is the
tagger's own help. `pipeline` runs the stages in order and `status` reports on
all of them. Starting and stopping the server is ./hikidashi's job, on the host.

Stdlib only. A stage's heavy imports happen inside its own main.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from app import logs
from app.config import settings

# command -> (module, what it does)
COMMANDS = {
    "import": ("app.importer", "import an Instagram export ZIP or directory"),
    "user": ("app.accounts", "add accounts, set passwords, make invite links"),
    "token": ("app.tokens", "make, list and revoke API and worker tokens"),
    "enrich": ("app.enrich", "fetch metadata, thumbnails and (--media) videos"),
    "transcribe": ("app.asr", "transcribe speech in downloaded videos"),
    "ocr": ("app.ocr", "read on-screen text from photos and silent reels"),
    "tag": ("app.tagging", "derive tags from hashtags, and (--llm) the model"),
    "recipe": ("app.recipes", "find recipes and read out ingredients and steps"),
    "embed": ("app.embed", "embed items for semantic search"),
    "places": ("app.places", "find places in captions, and put them on the map"),
    "export": ("app.export", "write recipes or the library out for other tools"),
    "worker": ("app.jobs", "run the stages on new reels as they arrive"),
    "ai": ("app.ai", "hosted AI: providers, keys, the switch, budget and usage"),
}

# After an optional import. Each stage feeds the next; OCR waits on transcripts,
# the tagger on both, recipes on the tagger's Food tag, and the embedder is
# best run on the tagger's summaries. Places need only captions, and are last
# because looking them up on a public geocoder is slow by its own rules.
PIPELINE: tuple[tuple[str, list[str]], ...] = (
    ("enrich", ["--media"]),
    ("transcribe", []),
    ("ocr", []),
    ("tag", ["--llm"]),
    ("recipe", []),
    ("embed", []),
    ("places", []),
)

LOCK = "pipeline.lock"

# On a desktop working for a server (HIKIDASHI_SERVER), these claim their
# reels from the server and send results back, as `hikidashi worker --once`
# would: run by hand, for a desktop that is not left working (#86). The
# commands that write the database run on the server only.
REMOTE_COMMANDS = {"transcribe": "asr", "ocr": "ocr", "tag": "tag", "recipe": "recipe"}
SERVER_COMMANDS = ("import", "enrich", "embed", "places", "user", "token", "export")
# `ai` runs anywhere: on the host it opens the database, and with --server it
# goes through the server's routes (#125).


class Busy(Exception):
    """Another pipeline holds the lock."""


@contextmanager
def pipeline_lock(run_dir: Path) -> Iterator[None]:
    """Hold the pipeline lock for the duration. Raises Busy if it is taken.

    flock, not a pidfile: the kernel drops it when the holder exits, however it
    exits, so a killed run never leaves a stale lock behind.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / LOCK
    with path.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            raise Busy(f.read().strip() or "?") from None
        f.truncate(0)
        f.write(f"{os.getpid()}\n")
        f.flush()
        try:
            yield
        finally:
            f.truncate(0)
            fcntl.flock(f, fcntl.LOCK_UN)


def pipeline_holder(run_dir: Path) -> str | None:
    """The pid running a pipeline, or None when none is."""
    try:
        with pipeline_lock(run_dir):
            return None
    except Busy as exc:
        return str(exc)


def run_command(name: str, argv: list[str]) -> int:
    """Run one stage in this process, the way `python -m` would."""
    module = importlib.import_module(COMMANDS[name][0] + ".__main__")
    main = cast(Callable[[list[str]], int], module.main)
    if name == "import":
        # The importer reads sys.argv whole, program name included.
        return main([f"hikidashi {name}", *argv])
    return main(argv)


def run_stage(name: str, argv: list[str]) -> int:
    """Run one stage in a child process, so its models are gone when it ends."""
    cmd = [sys.executable, "-m", COMMANDS[name][0], *argv]
    return subprocess.run(cmd).returncode


def pipeline(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="hikidashi pipeline",
        description="import (if given), then enrich --media, transcribe, ocr, "
        "tag --llm, recipe, embed and places, stopping at the first failure",
    )
    ap.add_argument("export", nargs="?", help="an export ZIP or directory to import first")
    names = [name for name, _ in PIPELINE]
    # Under Docker, ./hikidashi runs each part in the container with its tools.
    ap.add_argument(
        "--steps",
        metavar="LIST",
        help=f"only these, still in pipeline order: some of {','.join(names)}",
    )
    args = ap.parse_args(argv)

    wanted = names
    if args.steps:
        wanted = [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = sorted(set(wanted) - set(names))
        if unknown:
            ap.error(f"--steps: unknown {', '.join(unknown)}")
    steps = [(name, list(flags)) for name, flags in PIPELINE if name in wanted]
    if args.export:
        steps.insert(0, ("import", [args.export]))

    try:
        with pipeline_lock(settings.run_dir):
            for i, (name, flags) in enumerate(steps, 1):
                label = " ".join([name, *flags])
                print(f"\n[{i}/{len(steps)}] {label}", flush=True)
                if name == "ocr" and not shutil.which("tesseract"):
                    print("  skipped: tesseract is not installed", flush=True)
                    continue
                code = run_stage(name, flags)
                if code:
                    print(f"\n  pipeline stopped: {label} exited {code}", file=sys.stderr)
                    return code
    except Busy as exc:
        print(f"  another pipeline is running (pid {exc})", file=sys.stderr)
        return 1
    print("\n  pipeline finished")
    return 0


# --- status ------------------------------------------------------------------


def server_url() -> str:
    host = settings.host if settings.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    return f"http://{host}:{settings.port}"


def server_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/auth", timeout=2) as r:
            return bool(r.status == 200)
    except OSError:
        return False


def whisper_on_disk(models_dir: Path, model: str) -> bool:
    # faster-whisper's names map onto Systran repos: distil-small.en is
    # faster-distil-whisper-small.en, small.en is faster-whisper-small.en.
    repo = (
        "faster-distil-whisper-" + model.removeprefix("distil-")
        if model.startswith("distil-")
        else "faster-whisper-" + model
    )
    return any(models_dir.glob(f"models--*--{repo}/snapshots/*"))


def embedder_on_disk(models_dir: Path, model: str) -> bool:
    name = model.rsplit("/", 1)[-1]
    return any((models_dir / "fastembed").glob(f"models--*--{name}*/snapshots/*"))


def installed(module: str) -> bool:
    """Whether `module` could be imported here, without importing it."""
    return importlib.util.find_spec(module) is not None


def ago(seconds: float) -> str:
    """12 s, 4 min, 3 h, 2 days."""
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s"
    if s < 90 * 60:
        return f"{round(s / 60)} min"
    if s < 36 * 3600:
        return f"{round(s / 3600)} h"
    return f"{round(s / 86400)} days"


# What a worker is doing, in status.
DOING = {
    "enrich": "fetching",
    "asr": "transcribing",
    "ocr": "reading on-screen text",
    "tag": "tagging",
    "recipe": "reading recipes",
    "embed": "embedding",
}


def print_workers(conn: sqlite3.Connection, now: float | None = None) -> None:
    """Each worker: running, stopped, or gone quiet, which is not the same
    thing as stopped: a worker that is off said so, one that died did not."""
    from app.jobs.queue import workers

    now = time.time() if now is None else now
    found = workers(conn, now=now)
    if not found:
        print("  worker      none has checked in; ./hikidashi start runs one")
        return
    width = max(len(w["name"]) for w in found)
    for i, w in enumerate(found):
        label = "worker" if i == 0 else ""
        if w["state"] == "running":
            what = ", ".join(w["stages"]) or "no stages (HIKIDASHI_LOCAL_STAGES=none)"
            line = f"running · {what} · checked in {ago(now - w['seen_at'])} ago"
            if w["doing"]:
                line += f", {DOING.get(w['doing'], w['doing'])}"
        elif w["state"] == "stopped":
            line = f"stopped {ago(now - w['stopped_at'])} ago"
        else:
            # Silent for over STALE seconds: crashed, killed, or its host is off.
            line = (
                f"not responding: last checked in {ago(now - w['seen_at'])} ago, "
                "and never said it was stopping"
            )
        print(f"  {label:<10}  {w['name']:<{width}}  {line}")
        for stage, why in w["problems"].items():
            print(f"  {'':<10}  {'':<{width}}    {stage} cannot run: {why}")


def print_queue(conn: sqlite3.Connection) -> None:
    from app.jobs.queue import MAX_ATTEMPTS, queue

    print(f"\n  {'jobs':<10}  {'ready':>6} {'waiting':>8} {'working':>8} {'failed':>7}")
    stages = queue(conn)
    for q in stages:
        print(
            f"  {'  ' + q['stage']:<10}  {q['ready']:>6} {q['waiting']:>8} "
            f"{q['working']:>8} {q['failed']:>7}"
        )
    if next(q["without"] for q in stages if q["stage"] == "embed"):
        print(
            f"  {'':<10}  embed: no worker can transcribe or tag now, so new reels are "
            "embedded from their captions, and again as transcripts and summaries arrive"
        )
    print(
        f"  {'':<10}  failed: tried {MAX_ATTEMPTS} times; `hikidashi worker "
        "--retry-failed` tries again"
    )


def status(argv: list[str]) -> int:
    argparse.ArgumentParser(
        prog="hikidashi status",
        description="pipeline counts per stage, server up or down, and which models are ready",
    ).parse_args(argv)

    from app.asr import worker as asr
    from app.db import open_db
    from app.embed import worker as embed
    from app.enrich import worker as enrich
    from app.ocr import worker as ocr
    from app.places import worker as places
    from app.places.service import locating
    from app.recipes import worker as recipe
    from app.tagging import worker as tag

    if settings.server:
        # A desktop working for a server keeps no database (#35): the counts
        # are the server's.
        print(f"  server      {settings.server}; this machine works for it")
        print("  pipeline    on the server's Pipeline page, or `./hikidashi status` there")
        print_models()
        return 0

    url = server_url()
    print(f"  server      {'up at ' + url if server_up(url) else 'down'}")
    holder = pipeline_holder(settings.run_dir)
    print(f"  pipeline    {f'running (pid {holder})' if holder else 'idle'}")

    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    print_workers(conn)
    e, a, o, t, r, v, p = (m.summary(conn) for m in (enrich, asr, ocr, tag, recipe, embed, places))
    states = conn.execute(
        "SELECT state, COUNT(*) AS n FROM item GROUP BY state ORDER BY n DESC"
    ).fetchall()
    print(f"\n  items       {e['total']}" + "".join(f" · {r['n']} {r['state']}" for r in states))
    print(f"  videos      {e['media']} downloaded · {e['thumbs']} thumbnails")
    print(
        f"  transcribe  {a['transcribed']} transcribed · {a['silent']} no speech · "
        f"{a['waiting']} waiting · {a['failed']} failed"
    )
    print(
        f"  ocr         {o['text']} with text · {o['empty']} no text · "
        f"{o['waiting']} waiting · {o['failed']} failed"
    )
    print(
        f"  tag         {t['done']} done · {t['waiting']} waiting · "
        f"{t['waiting_asr'] + t['waiting_ocr']} waiting on transcript or OCR · "
        f"{t['failed']} failed · {t['untagged']} with no tag"
    )
    print(
        f"  recipe      {r['recipes']} recipes ({r['incomplete']} incomplete) · "
        f"{r['done']} read · {r['waiting']} waiting · {r['failed']} failed"
    )
    print(
        f"  embed       {v['vectors']}/{v['items']} embedded ({v['chunks']} transcript "
        f"chunks) · {v['behind']} behind"
    )
    print(
        f"  places      {p['reels']} reels name {p['places']} · {p['located']} on the map · "
        f"{p['approximate']} approximate · {p['missing']} not found · "
        f"{p['waiting']} waiting"
        + ("" if settings.geocoder else " (no geocoder)")
        + (" · looking up now" if locating(settings.run_dir) else "")
    )
    print_queue(conn)
    print_apis(conn)
    print_models()
    return 0


def print_apis(conn: sqlite3.Connection) -> None:
    """A line a hosted API (#125): on or off, and why it went off; in
    flight; this month's spend."""
    try:
        from app.ai import status as ai_status

        found = ai_status.apis(conn, settings.db_path.parent)
    except ImportError:
        return
    shown = [a for a in found if a.get("on") or a.get("reason") or a.get("spent")]
    if not shown:
        print("\n  hosted AI   off; workers process every reel (`hikidashi ai` to set it up)")
        return
    print()
    for i, a in enumerate(shown):
        label = "hosted AI" if i == 0 else ""
        if a.get("on") is None:  # a hosted worker on another machine
            line = f"reported {a['spent']:.2f} USD this month"
        elif a["on"]:
            budget = f" of {a['budget']:g}" if a.get("budget") else ""
            scope = f" for {a['scope']}" if a.get("scope") else ""
            line = (
                f"on{scope} · {a['in_flight']} in flight · {a['waiting']} waiting · "
                f"{a['spent']:.2f}{budget} USD this month"
            )
        else:
            line = "off" + (f": {a['reason']}" if a.get("reason") else "")
            line += f" · {a['spent']:.2f} USD this month"
        print(f"  {label:<10}  {a['name']}  {line}")


def print_models() -> None:
    """Which models are on disk here, and whether Ollama has the tagger's."""
    from app.embed.embedder import MODEL as EMBED_MODEL
    from app.tagging import llm

    def ready(ok: bool) -> str:
        return "downloaded" if ok else "not downloaded yet"

    print()
    if installed("fastembed"):
        print(
            f"  embedder    {EMBED_MODEL}: "
            f"{ready(embedder_on_disk(settings.models_dir, EMBED_MODEL))}"
        )
    # The server image has no Whisper and no Ollama (#86): the workers say how
    # their models are doing, above, and a profile resolved here would be this
    # container's, not theirs.
    if not installed("faster_whisper") and "OLLAMA_URL" not in os.environ:
        print("  models      Whisper and Ollama are the workers'; any trouble shows above")
        return

    from app.profiles import resolve

    try:
        profile = resolve(settings.profile)
    except ValueError as exc:
        print(f"  profile     {exc}")
        return

    print(f"  profile     {profile.name}")
    if installed("faster_whisper"):
        print(
            f"  whisper     {profile.asr_model}: "
            f"{ready(whisper_on_disk(settings.models_dir, profile.asr_model))}"
        )
    model = llm.Ollama(settings.ollama_url, profile.tag_model)
    try:
        state = "ready" if model.has_model() else "not pulled yet"
    except llm.OllamaError:
        state = f"no Ollama at {model.url}"
    print(f"  tagger      {model.model}: {state}")


def usage() -> str:
    width = max(map(len, COMMANDS)) + 2
    lines = [__doc__.splitlines()[0].removeprefix("CLI: "), "", "commands:"]
    lines += [f"  {name:<{width}}{help_}" for name, (_, help_) in COMMANDS.items()]
    lines += [
        f"  {'pipeline':<{width}}[<zip>] import, then every stage in order",
        f"  {'status':<{width}}counts per stage, server and models",
        "",
        "`hikidashi <command> --help` lists that command's flags.",
    ]
    return "\n".join(lines)


def remote(name: str, argv: list[str]) -> int:
    """`name`, run by hand for the server this machine works for."""
    server = settings.server
    if name in SERVER_COMMANDS:
        print(
            f"  {name} runs on the server, {server}; this machine works for it "
            "(HIKIDASHI_SERVER is set)",
            file=sys.stderr,
        )
        return 2
    stages = REMOTE_COMMANDS.get(name) or ",".join(REMOTE_COMMANDS.values())
    # tag --llm is how the tagger is asked for by hand; a worker always asks it.
    flags = [a for a in argv if not (name == "tag" and a == "--llm")]
    if flags:
        print(
            f"  for {server}, `hikidashi {name}` is `hikidashi worker --once --stages "
            f"{stages}`, which takes none of: {' '.join(flags)}",
            file=sys.stderr,
        )
        return 2
    from app.jobs.__main__ import main as worker

    # A name of its own: starting, a worker takes back what its name holds,
    # and this one must not take an always-on worker's reels on this machine.
    name = f"{settings.worker_name}-once"
    return worker(["--once", "--stages", stages, "--name", name, "--log", logs.ONCE_FILE])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 2
    name, rest = argv[0], argv[1:]
    help_asked = any(a in ("-h", "--help") for a in rest)
    if (
        settings.server
        and not help_asked
        and (name == "pipeline" or name in REMOTE_COMMANDS or name in SERVER_COMMANDS)
    ):
        return remote(name, rest)
    if name == "pipeline":
        return pipeline(rest)
    if name == "status":
        return status(rest)
    if name in COMMANDS:
        return run_command(name, rest)
    print(f"hikidashi: unknown command {name!r}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
