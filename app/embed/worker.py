"""The embedding worker.

Pulls every item whose vector is missing, behind the current `embed_v`, or made
from text that has since changed, embeds them a batch at a time, and commits
each batch with its `embed_v` stamps. A run can be killed at any point and
resumed by running it again.

Nothing waits on an earlier stage. An item the tagger has not reached yet is
embedded from its caption, and re-embedded once its summary arrives, because
the summary changes the digest. That costs one extra embedding per item, which
is milliseconds; waiting would leave fresh imports out of semantic search.

Each item is one vector of its summary and keywords (item_vec) and one per
stretch of its transcript and on-screen text (item_chunk_vec), written together.

Outcomes:

    embedded    vectors written, embed_v stamped
    empty       no text to embed; any old vectors removed, still stamped
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from .embedder import EMBED_V, chunks, digest, document

# Takes document texts, returns one packed float32 vector per text.
Embed = Callable[[list[str]], list[bytes]]

BATCH = 32


@dataclass
class Progress:
    total: int = 0
    embedded: int = 0
    empty: int = 0

    def line(self) -> str:
        return (
            f"{self.embedded + self.empty}/{self.total}  embedded {self.embedded}"
            f"  no text {self.empty}"
        )


@dataclass(frozen=True)
class Job:
    shortcode: str
    text: str | None
    digest: str | None
    chunks: tuple[str, ...] = ()


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
) -> list[Job]:
    """Items whose vector is missing or stale, newest first. `shortcodes`
    narrows it to those.

    Stale is decided here rather than in SQL: the digest is over the document
    text, and the document is built in Python. At this size that is a scan of
    a few hundred rows, not a cost worth an index.
    """
    rows = conn.execute(
        """SELECT item.shortcode, item.summary, item.keywords, item.caption,
                  item.transcript, item.ocr_text, item.embed_v, item_vec.digest
           FROM item LEFT JOIN item_vec ON item_vec.shortcode = item.shortcode
           ORDER BY item.saved_at IS NULL, item.saved_at DESC"""
    ).fetchall()
    only = set(shortcodes) if shortcodes is not None else None
    jobs = []
    for r in rows:
        if only is not None and r["shortcode"] not in only:
            continue
        text = document(r["summary"], r["keywords"], r["caption"])
        # Chunks only beside a document: an item with nothing to say what it
        # is about has no item_vec row to carry the digest.
        parts = tuple(chunks(r["transcript"], r["ocr_text"])) if text else ()
        d = digest(text, parts) if text else None
        if force or r["embed_v"] < EMBED_V or r["digest"] != d:
            jobs.append(Job(r["shortcode"], text, d, parts))
            if limit and len(jobs) >= limit:
                break
    return jobs


def _record(conn: sqlite3.Connection, jobs: list[Job], vectors: list[bytes]) -> None:
    """`vectors` holds each job's document vector, then its chunks', in order."""
    conn.execute("BEGIN")
    try:
        at = 0
        for job in jobs:
            vec, parts = vectors[at], vectors[at + 1 : at + 1 + len(job.chunks)]
            at += 1 + len(job.chunks)
            conn.execute(
                """INSERT INTO item_vec (shortcode, digest, embedding)
                   VALUES (?, ?, ?)
                   ON CONFLICT (shortcode) DO UPDATE SET
                     digest = excluded.digest, embedding = excluded.embedding""",
                (job.shortcode, job.digest, vec),
            )
            conn.execute("DELETE FROM item_chunk_vec WHERE shortcode = ?", (job.shortcode,))
            conn.executemany(
                "INSERT INTO item_chunk_vec (shortcode, seq, embedding) VALUES (?, ?, ?)",
                [(job.shortcode, i, v) for i, v in enumerate(parts)],
            )
        conn.executemany(
            "UPDATE item SET embed_v = ? WHERE shortcode = ?",
            [(EMBED_V, j.shortcode) for j in jobs],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _record_empty(conn: sqlite3.Connection, jobs: list[Job]) -> None:
    conn.execute("BEGIN")
    try:
        for table in ("item_vec", "item_chunk_vec"):
            conn.executemany(
                f"DELETE FROM {table} WHERE shortcode = ?", [(j.shortcode,) for j in jobs]
            )
        conn.executemany(
            "UPDATE item SET embed_v = ? WHERE shortcode = ?",
            [(EMBED_V, j.shortcode) for j in jobs],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def embed_all(
    conn: sqlite3.Connection,
    embed: Embed,
    *,
    limit: int | None = None,
    force: bool = False,
    batch: int = BATCH,
    shortcodes: list[str] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Embed every pending item, or every one of `shortcodes` that is
    pending. Safe to interrupt and re-run."""
    jobs = pending(conn, limit=limit, force=force, shortcodes=shortcodes)
    progress = Progress(total=len(jobs))

    empty = [j for j in jobs if not j.text]
    if empty:
        _record_empty(conn, empty)
        progress.empty = len(empty)
        if on_progress:
            on_progress(progress)

    todo = [j for j in jobs if j.text]
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        texts = [t for j in chunk for t in (j.text or "", *j.chunks)]
        vectors = embed(texts)
        if len(vectors) != len(texts):
            raise RuntimeError(f"asked for {len(texts)} vectors, got {len(vectors)}")
        _record(conn, chunk, vectors)
        progress.embedded += len(chunk)
        if on_progress:
            on_progress(progress)
    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    """How much of the library semantic search can see."""
    row = conn.execute(
        """SELECT COUNT(*) AS items,
                  (SELECT COUNT(*) FROM item_vec) AS vectors,
                  (SELECT COUNT(*) FROM item_chunk_vec) AS chunks,
                  SUM(embed_v < :v) AS behind
           FROM item""",
        {"v": EMBED_V},
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
