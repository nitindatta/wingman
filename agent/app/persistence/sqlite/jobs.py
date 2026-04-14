"""SQLite implementation of JobRepository.

Maps between domain `Job` models and the `jobs` table. Upsert is keyed on
`(provider, source_url)` (the table's UNIQUE constraint); on conflict the
existing row's mutable fields and `last_seen_at` are refreshed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.state.jobs import Job


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"],
        provider=row["provider"],
        source_url=row["source_url"],
        canonical_key=row["canonical_key"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        summary=row["summary"],
        payload=json.loads(row["payload_json"]),
        state=row["state"] if "state" in row.keys() else "discovered",
        discovered_at=datetime.fromisoformat(row["discovered_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
    )


class SqliteJobRepository:
    """JobRepository implementation backed by aiosqlite.

    Phase 1 scope: upsert by (provider, source_url), get by id, list by provider.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def upsert(
        self,
        *,
        provider: str,
        source_url: str,
        canonical_key: str,
        title: str,
        company: str,
        location: str | None,
        summary: str | None,
        payload: dict[str, Any],
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        existing = await self._db.execute(
            "SELECT id FROM jobs WHERE provider = ? AND source_url = ?",
            (provider, source_url),
        )
        existing_row = await existing.fetchone()
        await existing.close()

        if existing_row is not None:
            job_id = existing_row["id"]
            await self._db.execute(
                """
                UPDATE jobs
                   SET canonical_key = ?,
                       title = ?,
                       company = ?,
                       location = ?,
                       summary = ?,
                       payload_json = ?,
                       last_seen_at = ?
                 WHERE id = ?
                """,
                (
                    canonical_key,
                    title,
                    company,
                    location,
                    summary,
                    json.dumps(payload),
                    now,
                    job_id,
                ),
            )
        else:
            job_id = str(uuid.uuid4())
            await self._db.execute(
                """
                INSERT INTO jobs (
                    id, provider, source_url, canonical_key,
                    title, company, location, summary,
                    payload_json, discovered_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    provider,
                    source_url,
                    canonical_key,
                    title,
                    company,
                    location,
                    summary,
                    json.dumps(payload),
                    now,
                    now,
                ),
            )
        await self._db.commit()
        return job_id

    async def get(self, job_id: str) -> Job | None:
        cursor = await self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return _row_to_job(row) if row else None

    async def update_state(self, job_id: str, state: str) -> None:
        await self._db.execute(
            "UPDATE jobs SET state = ? WHERE id = ?",
            (state, job_id),
        )
        await self._db.commit()

    async def list_by_provider(self, provider: str, limit: int = 50, state: str | None = None) -> list[Job]:
        if state:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE provider = ? AND state = ? ORDER BY discovered_at DESC LIMIT ?",
                (provider, state, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE provider = ? ORDER BY discovered_at DESC LIMIT ?",
                (provider, limit),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_job(row) for row in rows]

    async def list_all(self, limit: int = 50, state: str | None = None) -> list[Job]:
        if state:
            cursor = await self._db.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY discovered_at DESC LIMIT ?",
                (state, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_job(row) for row in rows]
