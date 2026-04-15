"""SQLite-backed work queue repository."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

log = logging.getLogger("queue_repo")


@dataclass
class WorkQueueItem:
    id: str
    queue_type: str
    entity_id: str
    payload: dict
    status: str
    created_at: str


class SqliteQueueRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._conn = connection

    async def enqueue(
        self,
        queue_type: str,
        entity_id: str,
        payload: dict | None = None,
    ) -> str:
        """Insert a pending work item.

        Idempotent: skips if the entity already has a pending/processing item
        of the same type. Returns the new item id, or "" if skipped.
        """
        async with self._conn.execute(
            "SELECT id FROM work_queue "
            "WHERE queue_type=? AND entity_id=? AND status IN ('pending','processing')",
            (queue_type, entity_id),
        ) as cur:
            if await cur.fetchone():
                log.debug("[enqueue] skipped duplicate queue_type=%s entity_id=%s", queue_type, entity_id)
                return ""

        item_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO work_queue (id, queue_type, entity_id, payload_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (item_id, queue_type, entity_id, json.dumps(payload or {}), now),
        )
        await self._conn.commit()
        log.debug("[enqueue] enqueued id=%s queue_type=%s entity_id=%s", item_id, queue_type, entity_id)
        return item_id

    async def claim_next(self) -> WorkQueueItem | None:
        """Atomically claim the oldest pending item. Returns None if queue empty."""
        return await self.claim_next_of_types(None)

    async def claim_next_of_types(self, types: list[str] | None) -> WorkQueueItem | None:
        """Atomically claim the oldest pending item matching the given queue_types.

        Args:
            types: list of queue_type values to match, or None to match any.
        """
        if types is None:
            sql = (
                "SELECT id, queue_type, entity_id, payload_json, status, created_at "
                "FROM work_queue WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
            )
            params: tuple = ()
        else:
            placeholders = ",".join("?" * len(types))
            sql = (
                "SELECT id, queue_type, entity_id, payload_json, status, created_at "
                f"FROM work_queue WHERE status='pending' AND queue_type IN ({placeholders}) "
                "ORDER BY created_at ASC LIMIT 1"
            )
            params = tuple(types)

        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "UPDATE work_queue SET status='processing', started_at=? WHERE id=? AND status='pending'",
            (now, row[0]),
        )
        await self._conn.commit()

        return WorkQueueItem(
            id=row[0],
            queue_type=row[1],
            entity_id=row[2],
            payload=json.loads(row[3]),
            status="processing",
            created_at=row[5],
        )

    async def reset_stale(self, older_than_seconds: int = 600) -> int:
        """Reset items stuck in 'processing' back to 'pending'.

        Called on startup so items orphaned by a previous crash or restart
        are automatically re-queued rather than stuck forever.
        Returns the number of items reset.
        """
        cutoff = (datetime.now(UTC) - __import__("datetime").timedelta(seconds=older_than_seconds)).isoformat()
        cursor = await self._conn.execute(
            "UPDATE work_queue SET status='pending', started_at=NULL "
            "WHERE status='processing' AND started_at <= ?",
            (cutoff,),
        )
        await self._conn.commit()
        count = cursor.rowcount
        if count:
            log.warning("[queue] reset %d stale processing item(s) older than %ds", count, older_than_seconds)
        return count

    async def mark_done(self, item_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "UPDATE work_queue SET status='done', finished_at=? WHERE id=?",
            (now, item_id),
        )
        await self._conn.commit()

    async def mark_failed(self, item_id: str, error: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "UPDATE work_queue SET status='failed', finished_at=?, error=? WHERE id=?",
            (now, error[:1000], item_id),
        )
        await self._conn.commit()

    async def cancel_for_entity(self, entity_id: str) -> int:
        """Mark all pending/processing queue items for entity_id as cancelled (failed).

        Called when an application is cancelled by the user so the worker won't
        pick up the item even if it hasn't started yet.
        Returns number of items cancelled.
        """
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            "UPDATE work_queue SET status='failed', finished_at=?, error='cancelled by user' "
            "WHERE entity_id=? AND status IN ('pending','processing')",
            (now, entity_id),
        )
        await self._conn.commit()
        count = cursor.rowcount
        if count:
            log.info("[queue] cancelled %d item(s) for entity_id=%s", count, entity_id)
        return count
