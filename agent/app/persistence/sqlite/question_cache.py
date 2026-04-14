"""SQLite repository for the question/answer cache.

Stores human-approved answers to employer screening questions and provides
keyword-overlap search so answer_field.py can skip the LLM for familiar
questions.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import aiosqlite

# Words that carry no discriminating signal for question matching
_STOPWORDS = frozenset(
    "a an the and or but of in on at to for is are was were be been being "
    "have has had do does did will would could should may might shall can "
    "you your we our i my it its this that these those with from by about "
    "as up if not no yes".split()
)

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")


def normalize(text: str) -> str:
    """Lower-case, strip punctuation, remove stopwords, return sorted token set joined."""
    lower = text.lower()
    no_punct = _PUNCT_RE.sub(" ", lower)
    tokens = [t for t in no_punct.split() if t and t not in _STOPWORDS]
    return " ".join(sorted(tokens))


def _overlap_score(a: str, b: str) -> float:
    """Jaccard-like token overlap between two normalized strings."""
    sa = set(a.split())
    sb = set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


MATCH_THRESHOLD = 0.55  # ≥55% token overlap → cache hit


class SqliteQuestionCacheRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def save(
        self,
        question_raw: str,
        answer: str,
        field_type: str | None = None,
    ) -> str:
        """Upsert: if an identical normalized question already exists update its answer."""
        question_norm = normalize(question_raw)
        now = datetime.now(timezone.utc).isoformat()

        existing = await self._db.execute(
            "SELECT id FROM question_answer_cache WHERE question_norm = ?",
            (question_norm,),
        )
        row = await existing.fetchone()
        await existing.close()

        if row:
            entry_id = row["id"]
            await self._db.execute(
                "UPDATE question_answer_cache SET answer=?, field_type=?, created_at=? WHERE id=?",
                (answer, field_type, now, entry_id),
            )
        else:
            entry_id = str(uuid.uuid4())
            await self._db.execute(
                """
                INSERT INTO question_answer_cache
                    (id, question_raw, question_norm, answer, field_type, source, created_at)
                VALUES (?, ?, ?, ?, ?, 'human', ?)
                """,
                (entry_id, question_raw, question_norm, answer, field_type, now),
            )
        await self._db.commit()
        return entry_id

    async def find(self, question_raw: str) -> str | None:
        """Return the best-matching cached answer, or None if no match ≥ threshold."""
        needle_norm = normalize(question_raw)
        if not needle_norm:
            return None

        cursor = await self._db.execute(
            "SELECT id, question_norm, answer FROM question_answer_cache"
        )
        rows = await cursor.fetchall()
        await cursor.close()

        best_score = 0.0
        best_row = None
        for r in rows:
            score = _overlap_score(needle_norm, r["question_norm"])
            if score > best_score:
                best_score = score
                best_row = r

        if best_row and best_score >= MATCH_THRESHOLD:
            # Increment use_count
            await self._db.execute(
                "UPDATE question_answer_cache SET use_count = use_count + 1 WHERE id = ?",
                (best_row["id"],),
            )
            await self._db.commit()
            return best_row["answer"]

        return None

    async def list_all(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT id, question_raw, answer, field_type, created_at, use_count "
            "FROM question_answer_cache ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]
