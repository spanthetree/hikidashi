"""Ask your library: an answer from your own saves, citing the reels (#45).

Retrieval is the search that already exists (app/search): the question is
ranked like any query, over one person's library, and the best few reels go to
the model, each numbered, with what it knows of them: the summary, the caption,
the stretch of voiceover that matched (or the start of it), the text on screen,
the recipe and the places. The model answers from those alone, citing them as
[1], [2], and says when none of them covers the question.

The model is the person's own (app/assistant.py, ADR 0021). Stdlib only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app import search
from app.chat import Asks

TOP = 8  # reels given to the model
# How people ask about their saves, which says nothing about what they want.
ASKING = frozenset("save saved saving remember remembered find found reels".split())
CHARS = 700  # of each text field, at most
PREDICT = 700

SYSTEM = """\
You answer questions about someone's saved Instagram reels, using only the
numbered reels you are given. Each has a summary, its caption, what was said
and shown, and its recipe or places when it has them.

- Answer in the language of the question, briefly: a few sentences or a short
  list.
- Cite the reels you used with their numbers in square brackets, like [2],
  right after what they support.
- Use only what the reels say. Never add facts from elsewhere.
- If no reel answers the question, set found to false and leave answer empty.
Reply with JSON only."""


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "answer": {"type": "string"},
        },
        "required": ["found", "answer"],
    }


def _cut(text: str | None, n: int = CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def sources(
    conn: sqlite3.Connection,
    q: str,
    query_vec: bytes | None,
    user_id: int,
    *,
    top: int = TOP,
) -> list[dict[str, Any]]:
    """The reels the question is answered from, best first, with what the
    model is told of each, and when in it the question was said."""
    why: dict[str, dict[str, Any]] = {}
    hits = search.rank(conn, q, query_vec, user_id=user_id, explain=why)
    if len(hits) < top:
        # A question rarely has every one of its words in one reel ("what did
        # I save about Kyoto"): then each word that says what it is about.
        words = [w for w in search.content_tokens(q) if w.lower() not in ASKING]
        for w in words if len(words) > 1 or not hits else []:
            hits += [h for h in search.rank(conn, w, None, user_id=user_id) if h not in hits]
    hits = hits[:top]
    out = []
    for sc in hits:
        r = conn.execute(
            """SELECT summary, caption, transcript, transcript_segments, ocr_text
               FROM item WHERE shortcode = ?""",
            (sc,),
        ).fetchone()
        segs = json.loads(r["transcript_segments"] or "null")
        at = search.moment(segs, q, why.get(sc, {}).get("chunk")) if segs else None
        said = at["said"] if at else None
        recipe = conn.execute(
            f"""SELECT rn.title, rn.source, rn.user_id,
                       (SELECT group_concat(trim(coalesce(ri.quantity, '') || ' '
                                 || coalesce(ri.unit, '') || ' ' || ri.item), '; ')
                          FROM recipe_ingredient ri WHERE ri.shortcode = rn.shortcode
                           AND ri.source = rn.source AND ri.user_id = rn.user_id) AS ingredients,
                       rn.steps
                FROM {_recipes(user_id)} rn WHERE rn.shortcode = ? AND rn.hidden = 0""",
            (sc,),
        ).fetchone()
        places = [
            p[0] for p in conn.execute("SELECT text FROM item_place WHERE shortcode = ?", (sc,))
        ]
        out.append(
            {
                "shortcode": sc,
                "at_ms": at["at_ms"] if at else None,
                "summary": _cut(r["summary"], 300),
                "caption": _cut(r["caption"]),
                "said": _cut(said or r["transcript"]),
                "on_screen": _cut(r["ocr_text"], 300),
                "recipe": None
                if recipe is None
                else {
                    "title": recipe["title"],
                    "ingredients": _cut(recipe["ingredients"]),
                    "steps": _cut(" ".join(json.loads(recipe["steps"] or "[]"))),
                },
                "places": places[:6],
            }
        )
    return out


def _recipes(user_id: int) -> str:
    from app.recipes.service import now

    return now(user_id)


def prompt(q: str, found: list[dict[str, Any]]) -> str:
    parts = []
    for i, s in enumerate(found, 1):
        lines = [f"[{i}]"]
        for label, key in (
            ("Summary", "summary"),
            ("Caption", "caption"),
            ("Said", "said"),
            ("On screen", "on_screen"),
        ):
            if s[key]:
                lines.append(f"{label}: {s[key]}")
        if s["recipe"]:
            rec = s["recipe"]
            lines.append(
                f"Recipe: {rec['title'] or ''}. Ingredients: {rec['ingredients'] or '-'}. "
                f"Steps: {rec['steps'] or '-'}"
            )
        if s["places"]:
            lines.append("Places: " + "; ".join(s["places"]))
        parts.append("\n".join(lines))
    return "Reels:\n\n" + "\n\n".join(parts) + f"\n\nQuestion: {q}"


CITE = re.compile(r"\[(\d{1,2})\]")


def answer(q: str, found: list[dict[str, Any]], asks: Asks) -> dict[str, Any]:
    """{found, answer, cited}: the model's answer, and the reels it cited, in
    the order it first cited them. Numbers it made up are dropped."""
    if not found:
        return {"found": False, "answer": "", "cited": []}
    reply = json.loads(
        asks.ask(SYSTEM, prompt(q, found), schema=schema(), max_tokens=PREDICT, name="ask")
    )
    text = str(reply.get("answer") or "").strip()
    cited: list[int] = []
    for m in CITE.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= len(found) and n not in cited:
            cited.append(n)
    # A bracket the model invented points nowhere: drop it from the text.
    text = CITE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(found) else "", text)
    ok = bool(reply.get("found")) and bool(text)
    return {"found": ok, "answer": text if ok else "", "cited": cited if ok else []}
