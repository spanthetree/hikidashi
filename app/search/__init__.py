"""Search: FTS5 keywords and embedding similarity, blended.

Keyword search is exact and explainable, and loses the reel you describe in
other words. Vectors find "a vegetarian sandwich" in a reel captioned
Portobello banh mi, and are vague about exact phrases. So both run, and the
ranking is tiered:

    1. the query as an exact phrase        always first, keyword order
    2. every other hit from any list       weighted reciprocal rank fusion

The lists, and what each counts for:

    keyword_hits       every word, plus creators and hashtags       1
    semantic_hits      close to the summary and keywords, or a      1
                       stretch of the voiceover close to the query
    content_word_hits  every word that says what you are after,     CONTENT_WEIGHT
                       when no reel has all of them

Reciprocal rank fusion scores a hit weight / (K + rank) on each list it is on
and sums them. It uses ranks only, so bm25 and cosine scores never have to be
put on one scale, and a reel several lists like rises above one only one does.

Every constant here was set against graded queries over a real library
(data/search_eval.json, git-ignored): `python -m app.search.eval` scores a
change, and docs/search.md has the numbers.

Every list is of one person's library, filtered inside each query rather than
after, so the caps on the vector lists are spent on reels they can see.

Stdlib only. The query vector is passed in, and the scan needs only the
`vec_distance_cosine` function that `load_vec` registers, so without either
this is keyword search.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from itertools import combinations
from typing import Any

from app.library import LOCAL, mine

K = 60  # the usual RRF constant; damps the head of each list

# Transcript and on-screen text chunks (app/embed/embedder.py). Below a 0.5
# floor the chunks start outranking reels that are about the query.
MIN_CHUNK_SIMILARITY = 0.5
MAX_CHUNK_HITS = 12
# A chunk's similarity, less this, competes with the summary's for the reel's
# place on the semantic list. One stretch of speech matching is weaker evidence
# than the whole reel being about it. Fused as a list of their own, chunks
# counted twice for a reel with a voiceover, and a closer summary match
# without one lost to it (#20: place 0.86, now 0.88).
CHUNK_HANDICAP = 0.1

# What a reel matching every content word counts for, against 1 for one
# matching every word, or close in meaning. Measured flat from 0.25 to 0.75;
# at 1 a place query brings up every reel in the place.
CONTENT_WEIGHT = 0.5

# A vector hit must be at least this similar. Every item is some distance from
# every query, so without a floor a search for anything returns the whole
# library, ranked. Measured with embeddinggemma on the library: the median item
# sits near 0.1 for any query, on-topic reels at 0.3-0.5. Per model, not
# universal: change it with MODEL.
MIN_SIMILARITY = 0.27
# ...and no more than this many of them. A vague query ("that video about
# saving money") is near everything a little, and past this many the tail is
# noise. Not relative to the best match: one fluke at the top (a sandwich reel
# for a pizza query) would then cut the real answers.
MAX_VECTOR_HITS = 24


# Words that say how you are asking, not what you are after. A sentence-length
# query keeps only the rest for its content-word match: "that video where he
# says he's been baking for years" is looking for "baking", not for "that".
STOPWORDS = frozenset(
    """
a about all an and any are as at be been being but by can could did do does
doing for from had has have he her here him his how i if in into is it its
just me my no not of on one or our out over she so some than that the their
them then there these they this those to too up us very was we were what when
where which who whom why will with would you your
s t ll ve re d m don doesn didn isn wasn
video reel clip post thing stuff guy guys girl lady woman man someone person
says said saying talks talking shows showing show showed
""".split()
)

# Share of the library a word may appear in and still count in the
# content-word match. "best" is in a fifth of it, "tokyo" in a twentieth.
COMMON = 0.1

# Scripts written without spaces. unicode61 makes one token of a whole run of
# them, so "紅葉" never matches a caption that says "紅葉スポット": those are
# matched as substrings instead.
UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff66-\uff9f]")


def tokens(q: str) -> list[str]:
    return re.findall(r"\w+", q, re.UNICODE)


def content_tokens(q: str) -> list[str]:
    """The tokens worth matching on their own: no stopwords, no repeats."""
    seen, out = set(), []
    for t in tokens(q):
        low = t.lower()
        if low not in STOPWORDS and low not in seen:
            seen.add(low)
            out.append(t)
    return out


def fts_query(q: str) -> str | None:
    """Turn user text into a safe FTS5 MATCH expression.

    Tokens are quoted and ANDed, so punctuation and FTS operators in the input
    are data rather than syntax.
    """
    toks = tokens(q)
    return " ".join(f'"{t}"' for t in toks) if toks else None


def distinctive(conn: sqlite3.Connection, q: str, user_id: int = LOCAL) -> list[str]:
    """The content tokens of `q` that say what you are after.

    A word in more than COMMON of the person's library is dropped too: it finds
    nothing in particular, and a reel must not miss "the best ramen in
    Sapporo" for not saying "best". None when no word is left, or for a single
    word, which `fts_query` has already tried.
    """
    if len(tokens(q)) < 2:
        return []
    total = conn.execute("SELECT COUNT(*) FROM user_item WHERE user_id = ?", (user_id,)).fetchone()[
        0
    ]

    def rare(t: str) -> bool:
        n = conn.execute(
            f"""SELECT COUNT(*) FROM item_fts JOIN item ON item.rowid = item_fts.rowid
                WHERE item_fts MATCH ? AND {mine(user_id)}""",
            (f'"{t}"',),
        ).fetchone()[0]
        return bool(n <= 1 or n <= COMMON * total)

    return [t for t in content_tokens(q) if rare(t)]


def fts_content(conn: sqlite3.Connection, q: str, user_id: int = LOCAL) -> str | None:
    """The distinctive tokens ANDed: the query with the words that only say how
    you are asking taken out. None when there are none (see `distinctive`).

    ANDed, not ORed: one common adjective in a query about grooming a pet
    found 24 reels, and a query about board games with children pushed the
    real answer out with 48 reels about games (#20).
    """
    toks = distinctive(conn, q, user_id)
    return " AND ".join(f'"{t}"' for t in toks) if toks else None


def phrase_query(q: str) -> str | None:
    """The tokens as one FTS5 phrase: adjacent, in order. None for one token,
    where a phrase is no stronger than the plain match."""
    toks = tokens(q)
    return '"' + " ".join(toks) + '"' if len(toks) > 1 else None


def load_vec(conn: sqlite3.Connection) -> bool:
    """Register sqlite-vec's functions on this connection. False if unavailable."""
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError):
        return False
    return True


def _recipes(user_id: int) -> str:
    """SQL true for the recipe_fts rows of the recipes this person sees: their
    correction where they made one, else the model's."""
    uid = int(user_id)
    return f"""(recipe_fts.user_id = {uid}
                OR (recipe_fts.user_id = 0 AND NOT EXISTS (
                      SELECT 1 FROM recipe WHERE recipe.shortcode = recipe_fts.shortcode
                        AND recipe.source = 'user' AND recipe.user_id = {uid})))
               AND {mine(uid, "recipe_fts.shortcode")}"""


def _fts(conn: sqlite3.Connection, match: str, user_id: int) -> list[str]:
    """Item index first, then recipe titles and ingredients: a separate index,
    so a reel found by its recipe alone ranks after every reel that says the
    words itself."""
    ranked = [
        r[0]
        for r in conn.execute(
            f"""SELECT item.shortcode FROM item_fts
            JOIN item ON item.rowid = item_fts.rowid
            WHERE item_fts MATCH ? AND {mine(user_id)}
            ORDER BY bm25(item_fts)""",
            (match,),
        )
    ]
    seen = set(ranked)
    ranked += [
        r[0]
        for r in conn.execute(
            f"""SELECT shortcode FROM recipe_fts WHERE recipe_fts MATCH ? AND {_recipes(user_id)}
            ORDER BY bm25(recipe_fts)""",
            (match,),
        )
        if r[0] not in seen
    ]
    return ranked


def keyword_hits(conn: sqlite3.Connection, q: str, user_id: int = LOCAL) -> list[str]:
    """Shortcodes matching the keywords, best first, in tiers:

        1. every word, best bm25 first, then recipe titles and ingredients
        2. creator names and hashtag fragments, which FTS does not index: FTS
           matches whole tokens, so it finds #easyrecipe for "easyrecipe" but
           not for "recipe". "tokyo carspots" finds @tokyocarspots_example too.
           And the tags and games this person chose, which are theirs, not the
           index's.
        3. unspaced-script words as substrings of the text (see UNSPACED)

    A sentence rarely matches every one of its words: `content_word_hits` is
    what finds "the one where she says the sauce cooks while the roast rests".
    """
    like = f"%{q.strip()}%"
    fts = fts_query(q)
    if not fts:
        # Nothing word-like to search for: an @handle fragment, a shortcode.
        return [
            r[0]
            for r in conn.execute(
                f"""SELECT shortcode FROM item
                WHERE (author LIKE ? OR shortcode LIKE ?) AND {mine(user_id)}
                ORDER BY saved_at DESC""",
                (like, like),
            )
        ]
    ranked = _fts(conn, fts, user_id)
    seen = set(ranked)

    def add(rows: Iterable[sqlite3.Row]) -> None:
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0])
                ranked.append(r[0])

    compact = "%" + "".join(tokens(q)) + "%"
    add(
        conn.execute(
            """SELECT item.shortcode FROM item
           JOIN user_item ui ON ui.shortcode = item.shortcode AND ui.user_id = :uid
           WHERE author LIKE :like OR author_name LIKE :like
              OR clean_hashtags LIKE :like OR author LIKE :compact
              OR (SELECT name FROM tag WHERE tag.id = ui.tag_id) LIKE :like
              OR NULLIF(ui.game, '') LIKE :like
           ORDER BY item.saved_at DESC""",
            {"like": like, "compact": compact, "uid": user_id},
        )
    )
    for word in (t for t in tokens(q) if UNSPACED.search(t)):
        add(
            conn.execute(
                f"""SELECT shortcode FROM item
                WHERE (caption LIKE :w OR ocr_text LIKE :w OR transcript LIKE :w)
                  AND {mine(user_id)}
                ORDER BY saved_at DESC""",
                {"w": f"%{word}%"},
            )
        )
        # A recipe you retitled "究極のおうちカレー" is only in recipe_fts.
        add(
            conn.execute(
                f"""SELECT shortcode FROM recipe_fts
                WHERE (title LIKE :w OR ingredients LIKE :w) AND {_recipes(user_id)}""",
                {"w": f"%{word}%"},
            )
        )
    return ranked


def content_word_hits(
    conn: sqlite3.Connection, q: str, user_id: int = LOCAL, *, loose: bool = False
) -> list[str]:
    """Reels matching every distinctive word of `q` (see `fts_content`), best
    bm25 first. Weaker than matching every word, so fused at CONTENT_WEIGHT.

    `loose`, for a search without vectors, settles for any two of the words
    when no reel has them all: nothing else would find a sentence. With
    vectors it only crowds out what they found (#20: keyword-only nDCG@10 0.58
    strict, 0.73 loose; blended 0.92 strict, 0.91 loose).
    """
    match = fts_content(conn, q, user_id)
    hits = _fts(conn, match, user_id) if match else []
    toks = distinctive(conn, q, user_id) if loose and not hits else []
    if len(toks) > 2:
        hits = _fts(
            conn, " OR ".join(f'("{a}" AND "{b}")' for a, b in combinations(toks, 2)), user_id
        )
    return hits


def phrase_hits(conn: sqlite3.Connection, q: str, user_id: int = LOCAL) -> set[str]:
    phrase = phrase_query(q)
    if not phrase:
        return set()
    return {
        r[0]
        for r in conn.execute(
            f"""SELECT item.shortcode FROM item_fts
            JOIN item ON item.rowid = item_fts.rowid
            WHERE item_fts MATCH ? AND {mine(user_id)}""",
            (phrase,),
        )
    }


def vector_hits(
    conn: sqlite3.Connection, query_vec: bytes, user_id: int = LOCAL
) -> list[tuple[str, float]]:
    """(shortcode, similarity) close enough to the query, most similar first.

    A brute-force scan: every vector, one cosine each. At a few hundred items
    that is under a millisecond, and an index would only add a way to be stale.
    """
    rows = conn.execute(
        # The length check skips vectors from a model of another size, left
        # behind until the worker re-embeds; comparing them is an error.
        f"""SELECT shortcode, 1 - vec_distance_cosine(embedding, :q) AS sim
            FROM item_vec WHERE length(embedding) = length(:q)
              AND {mine(user_id, "shortcode")}
            ORDER BY sim DESC LIMIT :n""",
        {"q": query_vec, "n": MAX_VECTOR_HITS},
    ).fetchall()
    return [(r[0], r[1]) for r in rows if r[1] >= MIN_SIMILARITY]


def chunk_hits(
    conn: sqlite3.Connection, query_vec: bytes, user_id: int = LOCAL
) -> list[tuple[str, float]]:
    """(shortcode, similarity of its closest chunk) for the items whose
    transcript or on-screen text says something close to the query.

    The floor is higher than MIN_SIMILARITY: a 60-word stretch of speech is
    near many queries a little, and a reel should be here for one thing said
    that matches, not for talking about food in general.
    """
    rows = conn.execute(
        f"""SELECT shortcode, MAX(1 - vec_distance_cosine(embedding, :q)) AS sim
            FROM item_chunk_vec WHERE length(embedding) = length(:q)
              AND {mine(user_id, "shortcode")}
            GROUP BY shortcode ORDER BY sim DESC LIMIT :n""",
        {"q": query_vec, "n": MAX_CHUNK_HITS},
    ).fetchall()
    return [(r[0], r[1]) for r in rows if r[1] >= MIN_CHUNK_SIMILARITY]


def similar(
    conn: sqlite3.Connection, shortcode: str, user_id: int = LOCAL, limit: int = 12
) -> list[str]:
    """The reels in this person's library closest in meaning to `shortcode`,
    most similar first, without it (#53). Its own summary vector is the query,
    so the same floor as a typed query keeps unrelated reels out."""
    rows = conn.execute(
        f"""SELECT o.shortcode, 1 - vec_distance_cosine(o.embedding, me.embedding) AS sim
            FROM item_vec me JOIN item_vec o
              ON o.shortcode != me.shortcode AND length(o.embedding) = length(me.embedding)
            WHERE me.shortcode = :sc AND {mine(user_id, "o.shortcode")}
            ORDER BY sim DESC LIMIT :n""",
        {"sc": shortcode, "n": limit},
    ).fetchall()
    return [r[0] for r in rows if r[1] >= MIN_SIMILARITY]


def semantic_hits(
    conn: sqlite3.Connection, query_vec: bytes, user_id: int = LOCAL
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """The reels close to the query in meaning, most similar first: by their
    summary, or by their closest chunk less CHUNK_HANDICAP, whichever is higher.
    Also the summary and chunk similarities, for `explain`."""
    summary = dict(vector_hits(conn, query_vec, user_id))
    spoken = dict(chunk_hits(conn, query_vec, user_id))
    best = dict(summary)
    for sc, sim in spoken.items():
        best[sc] = max(best.get(sc, -1.0), sim - CHUNK_HANDICAP)
    return sorted(best, key=lambda sc: -best[sc])[:MAX_VECTOR_HITS], summary, spoken


def fuse(*ranked: list[str], weights: tuple[float, ...] | None = None) -> list[str]:
    """Reciprocal rank fusion of several best-first lists, each counting for
    its weight (default 1)."""
    score: dict[str, float] = {}
    for i, hits in enumerate(ranked):
        w = weights[i] if weights else 1.0
        for rank, sc in enumerate(hits):
            score[sc] = score.get(sc, 0.0) + w / (K + rank + 1)
    return sorted(score, key=lambda sc: -score[sc])


def rank(
    conn: sqlite3.Connection,
    q: str,
    query_vec: bytes | None = None,
    *,
    user_id: int = LOCAL,
    explain: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Every shortcode in this person's library matching `q`, best first.

    `query_vec` is the query's embedding, or None to search keywords only; the
    connection must have had `load_vec` run on it to use one. `explain`, when
    given, is filled with why each hit matched.
    """
    keywords = keyword_hits(conn, q, user_id)
    # Only when no reel has every word: if one does, it is the better answer.
    content = [] if keywords else content_word_hits(conn, q, user_id, loose=query_vec is None)
    semantic, sims, spoken = semantic_hits(conn, query_vec, user_id) if query_vec else ([], {}, {})
    if query_vec is not None and not (keywords or content or semantic):
        # Nothing at all: rather than an empty page, what keyword-only search
        # would show, any two of the words (#57's demo voiceover queries).
        content = content_word_hits(conn, q, user_id, loose=True)
    fused = fuse(keywords, semantic, content, weights=(1.0, 1.0, CONTENT_WEIGHT))
    exact = phrase_hits(conn, q, user_id)
    # sorted() is stable, so each tier keeps its fused order.
    ranked = sorted(fused, key=lambda sc: sc not in exact)

    if explain is not None:
        kw, close = set(keywords) | set(content), set(semantic)
        for sc in ranked:
            near = sc in close
            best = max(sims.get(sc, -1.0), spoken.get(sc, -1.0)) if near else -1.0
            explain[sc] = {
                "match": [
                    m
                    for m, on in (
                        ("phrase", sc in exact),
                        ("keyword", sc in kw),
                        ("semantic", near and sc in sims),
                        ("transcript", near and sc in spoken),
                    )
                    if on
                ],
                "similarity": round(best, 3) if best > -1 else None,
            }
    return ranked
