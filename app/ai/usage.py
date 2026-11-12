"""What the hosted APIs used and cost, the budget, and estimates (#125).

Every request is recorded in `hosted_usage` against whoever's API made it,
with an estimated cost from PRICES: a dated table in code, since providers
publish prices on web pages, not in their APIs. A model not in it costs 0 here
and is flagged, so a budget cannot be relied on for it.

The budget is per month (UTC) and per API. On "All users" the server's API has
one budget for its key; its use is split between the savers of each reel who
rely on it, so the admin can see who it was spent on.

Estimates, for the backlog shown when a switch goes on:
    asr      from item.duration
    ocr      from the frames it would send (only for reels with no speech,
             which is not known until transcription: counted for reels
             with no media, and given as the high end for the rest)
    tag      from the text known so far, plus the prompt
    recipe   a range: none of the reels are recipes, to all of them

Stdlib only.
"""

from __future__ import annotations

import calendar
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from app.providers.client import Usage

from .config import Api

CHECKED = "2026-11-12"


@dataclass(frozen=True)
class Price:
    """USD: per million tokens in, out and cached in; per minute of audio."""

    input: float = 0.0
    output: float = 0.0
    cached: float | None = None
    minute: float = 0.0


# provider:model. To be checked against each provider's pricing page, with
# the date above; docs/operation.md lists the sources.
PRICES: dict[str, Price] = {
    # https://developers.openai.com/api/docs/pricing
    "openai:gpt-transcribe": Price(minute=0.0045),
    "openai:gpt-live-transcribe": Price(minute=0.017),
    "openai:whisper-1": Price(minute=0.006),  # shuts down 2027-02-26
    "openai:gpt-4o-transcribe": Price(minute=0.006),  # shuts down 2027-02-26
    "openai:gpt-4o-mini-transcribe": Price(minute=0.003),  # shuts down 2027-02-26
    "openai:gpt-6-luna": Price(input=0.10, output=0.50, cached=0.01),
    "openai:gpt-5.6-luna": Price(input=0.20, output=1.20, cached=0.02),
    # https://groq.com/pricing; to check again before relying on a budget
    "groq:whisper-large-v3-turbo": Price(minute=0.04 / 60),
    "groq:whisper-large-v3": Price(minute=0.111 / 60),
    "groq:openai/gpt-oss-20b": Price(input=0.075, output=0.30),
    "groq:meta-llama/llama-4-scout-17b-16e-instruct": Price(input=0.11, output=0.34),
}

# Rough sizes, for estimates only.
PROMPT_TOKENS = {"tag": 900, "game": 500, "recipe_check": 350, "recipe": 450, "ocr": 250}
OUTPUT_TOKENS = {"tag": 80, "recipe_check": 30, "recipe": 600, "ocr": 80}
IMAGE_TOKENS = 85  # one image at detail low
FRAMES = 6


def price(model: str) -> Price | None:
    return PRICES.get(model)


def cost(model: str, used: Usage) -> float:
    """Estimated USD for what one request used."""
    p = PRICES.get(model)
    if p is None:
        return 0.0
    fresh = max(0, used.input_tokens - used.cached_tokens)
    cached_rate = p.cached if p.cached is not None else p.input
    return (
        fresh * p.input / 1e6
        + used.cached_tokens * cached_rate / 1e6
        + used.output_tokens * p.output / 1e6
        + used.audio_seconds / 60 * p.minute
    )


def record(
    conn: sqlite3.Connection,
    api: str,
    owner: int | None,
    stage: str,
    model: str,
    used: Usage,
    *,
    requests: int = 1,
    shortcode: str | None = None,
    usd: float | None = None,
    at: int | None = None,
) -> float:
    """Record one stage's use on one reel. Returns its estimated cost."""
    usd = cost(model, used) if usd is None else usd
    conn.execute(
        """INSERT INTO hosted_usage (at, api, user_id, shortcode, stage, model, requests,
             input_tokens, output_tokens, cached_tokens, audio_seconds, images, usd)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            int(at if at is not None else time.time()),
            api,
            owner,
            shortcode,
            stage,
            model,
            requests,
            used.input_tokens,
            used.output_tokens,
            used.cached_tokens,
            used.audio_seconds,
            used.images,
            usd,
        ),
    )
    return usd


def month_bounds(month: str | None = None, now: float | None = None) -> tuple[int, int, str]:
    """The UTC month `YYYY-MM` (this one by default) as [start, end) seconds."""
    if month is None:
        t = time.gmtime(now if now is not None else time.time())
        y, m = t.tm_year, t.tm_mon
    else:
        try:
            y, m = (int(x) for x in month.split("-"))
            if not 1 <= m <= 12:
                raise ValueError
        except ValueError:
            raise ValueError("a month is YYYY-MM") from None
    start = calendar.timegm((y, m, 1, 0, 0, 0))
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, calendar.timegm((ny, nm, 1, 0, 0, 0)), f"{y:04d}-{m:02d}"


def spent(conn: sqlite3.Connection, api: str, *, now: float | None = None) -> float:
    """This month's estimated spend on one API."""
    start, end, _ = month_bounds(now=now)
    row = conn.execute(
        "SELECT COALESCE(SUM(usd), 0) FROM hosted_usage WHERE api = ? AND at >= ? AND at < ?",
        (api, start, end),
    ).fetchone()
    return float(row[0])


def report(
    conn: sqlite3.Connection, *, api: str | None = None, month: str | None = None
) -> dict[str, Any]:
    """A month's use: per API, per stage and model, and (for the server's API)
    split between the users it was spent on."""
    start, end, label = month_bounds(month)
    where = "at >= ? AND at < ?" + (" AND api = ?" if api else "")
    args: tuple[Any, ...] = (start, end, api) if api else (start, end)
    rows = conn.execute(
        f"""SELECT api, stage, model, SUM(requests) AS requests,
                   SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                   SUM(cached_tokens) AS cached_tokens, SUM(audio_seconds) AS audio_seconds,
                   SUM(images) AS images, SUM(usd) AS usd, COUNT(DISTINCT shortcode) AS reels
            FROM hosted_usage WHERE {where}
            GROUP BY api, stage, model ORDER BY api, stage, model""",
        args,
    ).fetchall()
    per_user = conn.execute(
        f"""WITH spend AS (
              SELECT shortcode, SUM(usd) AS usd FROM hosted_usage
              WHERE {where} AND api = 'api:server' AND shortcode IS NOT NULL
              GROUP BY shortcode),
            shares AS (
              SELECT ui.user_id, s.usd * 1.0 /
                     (SELECT COUNT(*) FROM user_item x WHERE x.shortcode = s.shortcode) AS usd
              FROM spend s JOIN user_item ui ON ui.shortcode = s.shortcode)
            SELECT u.username, ROUND(SUM(shares.usd), 6) AS usd
            FROM shares JOIN user u ON u.id = shares.user_id
            GROUP BY u.id ORDER BY usd DESC""",
        args,
    ).fetchall()
    unpriced = sorted({r["model"] for r in rows if r["model"] not in PRICES})
    return {
        "month": label,
        "rows": [dict(r) for r in rows],
        "total_usd": round(sum(r["usd"] or 0 for r in rows), 6),
        "server_by_user": [dict(r) for r in per_user],
        "unpriced": unpriced,
        "prices_checked": CHECKED,
    }


def estimate(conn: sqlite3.Connection, api: Api, codes: list[str]) -> dict[str, Any]:
    """What processing `codes` on `api` might cost: low and high, USD."""
    if not codes:
        return {"usd_low": 0.0, "usd_high": 0.0, "unpriced": []}
    import json

    rows = conn.execute(
        """SELECT shortcode, duration, media_path, asr_v, transcript, caption, ocr_text
           FROM item WHERE shortcode IN (SELECT value FROM json_each(?))""",
        (json.dumps(codes),),
    ).fetchall()
    from app.asr.transcriber import ASR_V

    low = high = 0.0
    unpriced: set[str] = set()

    def model(stage: str) -> str:
        st = api.stages[stage]
        name = f"{st.provider.value}:{api.model(stage)}" if st.preset else "?"
        if name not in PRICES:
            unpriced.add(name)
        return name

    m_asr, m_ocr, m_tag, m_recipe = (model(s) for s in ("asr", "ocr", "tag", "recipe"))
    for r in rows:
        needs_asr = r["media_path"] is not None and r["asr_v"] < ASR_V
        if needs_asr:
            c = cost(m_asr, Usage(audio_seconds=float(r["duration"] or 30)))
            low += c
            high += c
        ocr = cost(
            m_ocr,
            Usage(
                input_tokens=PROMPT_TOKENS["ocr"] + FRAMES * IMAGE_TOKENS,
                output_tokens=OUTPUT_TOKENS["ocr"],
            ),
        )
        if r["media_path"] is None:
            low += ocr
        high += ocr
        text = sum(len(r[k] or "") for k in ("caption", "transcript", "ocr_text")) // 4
        if needs_asr:
            text += int((r["duration"] or 30) * 2.5 * 1.3)  # words a second, tokens a word
        tag = cost(
            m_tag,
            Usage(input_tokens=PROMPT_TOKENS["tag"] + text, output_tokens=OUTPUT_TOKENS["tag"]),
        )
        low += tag
        high += tag
        high += cost(
            m_recipe,
            Usage(
                input_tokens=PROMPT_TOKENS["recipe_check"] + PROMPT_TOKENS["recipe"] + 2 * text,
                output_tokens=OUTPUT_TOKENS["recipe_check"] + OUTPUT_TOKENS["recipe"],
            ),
        )
    return {
        "usd_low": round(low, 4),
        "usd_high": round(high, 4),
        "unpriced": sorted(unpriced),
    }
