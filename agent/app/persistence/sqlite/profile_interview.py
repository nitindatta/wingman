"""SQLite repository for profile interview sessions and drafts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from app.state.profile_interview import ProfileInterviewState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteProfileInterviewRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def create(self, state: ProfileInterviewState) -> str:
        now = _now()
        await self._conn.execute(
            """
            INSERT INTO profile_interview_sessions
                (id, source_profile_path, target_profile_path, status, current_item_id,
                 current_question, current_gap, state_json, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.session_id,
                state.source_profile_path,
                state.target_profile_path,
                state.status,
                state.current_item_id,
                state.current_question,
                state.current_gap,
                state.model_dump_json(),
                now,
                now,
            ),
        )
        await self._conn.commit()
        return state.session_id

    async def get(self, session_id: str) -> ProfileInterviewState | None:
        cursor = await self._conn.execute(
            "SELECT state_json FROM profile_interview_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return ProfileInterviewState.model_validate_json(row["state_json"])

    async def get_active(self) -> ProfileInterviewState | None:
        cursor = await self._conn.execute(
            """
            SELECT state_json
            FROM profile_interview_sessions
            WHERE finished_at IS NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return ProfileInterviewState.model_validate_json(row["state_json"])

    async def save_state(self, state: ProfileInterviewState) -> None:
        now = _now()
        finished_at = now if state.status in {"completed", "error"} else None
        await self._conn.execute(
            """
            UPDATE profile_interview_sessions
            SET status = ?, current_item_id = ?, current_question = ?, current_gap = ?,
                state_json = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
            WHERE id = ?
            """,
            (
                state.status,
                state.current_item_id,
                state.current_question,
                state.current_gap,
                state.model_dump_json(),
                now,
                finished_at,
                state.session_id,
            ),
        )
        await self._conn.commit()

    async def record_turn(
        self,
        *,
        session_id: str,
        item_id: str,
        question_id: str,
        question_text: str,
        user_answer: str,
        interpreted_answer: dict[str, object],
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO profile_interview_turns
                (id, session_id, item_id, question_id, question_text, user_answer,
                 interpreted_answer_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                item_id,
                question_id,
                question_text,
                user_answer,
                json.dumps(interpreted_answer),
                _now(),
            ),
        )
        await self._conn.commit()

    async def record_draft(
        self,
        *,
        session_id: str,
        item_id: str,
        status: str,
        completeness_score: float,
        item_json: str,
        gap_summary_json: str,
    ) -> None:
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM profile_interview_item_drafts
            WHERE session_id = ? AND item_id = ?
            """,
            (session_id, item_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        version = int(row[0]) + 1 if row is not None else 1

        await self._conn.execute(
            """
            INSERT INTO profile_interview_item_drafts
                (id, session_id, item_id, version, status, completeness_score, item_json,
                 gap_summary_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                session_id,
                item_id,
                version,
                status,
                completeness_score,
                item_json,
                gap_summary_json,
                _now(),
            ),
        )
        await self._conn.commit()
