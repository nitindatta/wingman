"""SQLite repository for applications and drafts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from app.state.prepare import Application, Draft


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteApplicationRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(
        self,
        *,
        job_id: str,
        source_provider: str,
        source_url: str,
    ) -> str:
        app_id = str(uuid.uuid4())
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO applications
                (id, job_id, source_provider, source_url, state,
                 approval_required, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'prepared', 1, ?, ?)
            """,
            (app_id, job_id, source_provider, source_url, now, now),
        )
        await self._conn.commit()
        return app_id

    async def update_state(self, app_id: str, state: str) -> None:
        await self._conn.execute(
            "UPDATE applications SET state = ?, updated_at = ? WHERE id = ?",
            (state, _now(), app_id),
        )
        await self._conn.commit()

    async def get(self, app_id: str) -> Application | None:
        async with self._conn.execute(
            "SELECT id, job_id, source_provider, source_url, state, created_at, updated_at "
            "FROM applications WHERE id = ?",
            (app_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Application(
            id=row[0],
            job_id=row[1],
            source_provider=row[2],
            source_url=row[3],
            state=row[4],
            created_at=datetime.fromisoformat(row[5]),
            updated_at=datetime.fromisoformat(row[6]),
        )

    async def list_all(self, limit: int = 50, state: str | None = None) -> list[Application]:
        if state:
            sql = (
                "SELECT id, job_id, source_provider, source_url, state, created_at, updated_at "
                "FROM applications WHERE state = ? ORDER BY created_at DESC LIMIT ?"
            )
            args = (state, limit)
        else:
            sql = (
                "SELECT id, job_id, source_provider, source_url, state, created_at, updated_at "
                "FROM applications ORDER BY created_at DESC LIMIT ?"
            )
            args = (limit,)
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [
            Application(
                id=r[0],
                job_id=r[1],
                source_provider=r[2],
                source_url=r[3],
                state=r[4],
                created_at=datetime.fromisoformat(r[5]),
                updated_at=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]


class SqliteDraftRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(
        self,
        *,
        application_id: str,
        draft_type: str,
        generator: str,
        content: str,
        question_fingerprint: str | None = None,
    ) -> str:
        draft_id = str(uuid.uuid4())
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO drafts
                (id, application_id, draft_type, question_fingerprint,
                 generator, content, version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (draft_id, application_id, draft_type, question_fingerprint, generator, content, now),
        )
        await self._conn.commit()
        return draft_id

    async def update_content(self, draft_id: str, content: str) -> None:
        await self._conn.execute(
            "UPDATE drafts SET content = ?, version = version + 1 WHERE id = ?",
            (content, draft_id),
        )
        await self._conn.commit()

    async def list_for_application(self, application_id: str) -> list[Draft]:
        async with self._conn.execute(
            "SELECT id, application_id, draft_type, question_fingerprint, "
            "generator, content, version, created_at "
            "FROM drafts WHERE application_id = ? ORDER BY created_at",
            (application_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Draft(
                id=r[0],
                application_id=r[1],
                draft_type=r[2],
                question_fingerprint=r[3],
                generator=r[4],
                content=r[5],
                version=r[6],
                created_at=datetime.fromisoformat(r[7]),
            )
            for r in rows
        ]
