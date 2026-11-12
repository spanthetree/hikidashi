"""Run the real server on a throwaway seeded library, for the browser tests.

    python3 -m tests.serve --port 8093           # tests/seed.py's library
    python3 -m tests.serve --port 8094 --empty   # a first run: nobody yet

--upload PATH copies the seed's upload export there, for a test to import.
--setup-token PATH writes the one-time setup token there, which a real server
only logs, so a test can claim an --empty library through the page.

Everything lives in a new temporary folder, removed on exit, so the tests can
sign in, import and edit without touching data/. Accounts are on
(HIKIDASHI_AUTH=local). The locator and the geocoder stay off: nothing leaves
the machine. Needs the web dependencies and argon2-cffi, and the front end
built into app/web (`npm run build --prefix frontend`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tests.serve")
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--empty", action="store_true", help="a library nobody has claimed")
    ap.add_argument("--upload", type=Path, help="copy the upload export here")
    ap.add_argument("--setup-token", type=Path, help="write the setup token here")
    ap.add_argument(
        "--fake-ai",
        type=int,
        metavar="PORT",
        help="a fake OpenAI-compatible API on this port, and the hosted APIs (#125) "
        "running against it, as the server's worker runs them",
    )
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="hikidashi-e2e-") as tmp:
        root = Path(tmp)
        # Before anything imports app.config, which reads these once.
        os.environ.update(
            {
                "HIKIDASHI_DB": str(root / "hikidashi.sqlite3"),
                "HIKIDASHI_MEDIA": str(root / "media"),
                "HIKIDASHI_IMPORTS": str(root / "imports"),
                "HIKIDASHI_MODELS": str(root / "models"),
                "HIKIDASHI_LOGS": str(root / "logs"),
                "HIKIDASHI_RUN": str(root / "run"),
                "HIKIDASHI_AUTH": "local",
                "HIKIDASHI_GEOCODER": "",
                "HIKIDASHI_LOCATE_EVERY": "0",
            }
        )
        from tests import seed

        built = seed.build(root, people=not args.empty)
        if args.upload:
            args.upload.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(built.upload, args.upload)
        print(f"seeded {root}", flush=True)

        import uvicorn

        from app.api import main as server

        if args.fake_ai:
            _fake_ai(args.fake_ai, root)

        if args.setup_token:
            args.setup_token.parent.mkdir(parents=True, exist_ok=True)
            args.setup_token.write_text(server.SETUP_TOKEN)
        uvicorn.run(server.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _fake_ai(port: int, root: Path) -> None:
    """The fake provider, and the hosted APIs' host polling every second.
    Without ffmpeg (the browser tests' image has none), audio and frames are
    the media files' bytes: the fake reads neither."""
    import shutil as sh
    from unittest import mock

    from app.ai import runner
    from app.db import connect
    from tests.fake_openai import Fake, stage_reply

    Fake(chat_reply=stage_reply, key=None).start(port)
    if not sh.which("ffmpeg"):
        mock.patch("app.asr.hosted.encode", lambda path, **kw: b"fLaC" + path.read_bytes()).start()
        mock.patch(
            "app.ocr.hosted.frames", lambda row, media: [(media / row["thumb_path"]).read_bytes()]
        ).start()
    db = root / "hikidashi.sqlite3"
    # The seed leaves reels unfetched, so no API would take any. Bob's, as
    # the fetch would leave them: ready for his API in the browser test.
    from app.enrich.fetcher import FETCH_V

    conn = connect(db)
    conn.execute(
        """UPDATE item SET fetch_v = ? WHERE state != 'failed' AND media_path IS NOT NULL
             AND shortcode IN (SELECT shortcode FROM user_item JOIN user ON user.id = user_id
                               WHERE username = 'bob')""",
        (FETCH_V,),
    )
    conn.close()
    runner.Host(lambda: connect(db), root, root / "media", idle=1.0).start()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
