from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DeliveryClaimStatus(StrEnum):
    ACQUIRED = 'acquired'
    DELIVERED = 'delivered'
    PROCESSING = 'processing'


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    status: DeliveryClaimStatus
    message_id: int | None = None


class NotificationDeliveryStore:
    def __init__(self, path: Path, *, lease_seconds: float = 120) -> None:
        self._path = path
        self._lease_seconds = lease_seconds
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def claim(self, idempotency_key: str) -> DeliveryClaim:
        async with self._lock:
            connection = await self._connection_or_open()
            return await asyncio.to_thread(self._claim_sync, connection, idempotency_key)

    async def mark_delivered(self, idempotency_key: str, *, message_id: int | None) -> None:
        async with self._lock:
            connection = await self._connection_or_open()
            await asyncio.to_thread(self._mark_delivered_sync, connection, idempotency_key, message_id)

    async def release(self, idempotency_key: str) -> None:
        async with self._lock:
            connection = await self._connection_or_open()
            await asyncio.to_thread(self._release_sync, connection, idempotency_key)

    async def aclose(self) -> None:
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await asyncio.to_thread(connection.close)

    async def _connection_or_open(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = await asyncio.to_thread(self._open_sync)
        return self._connection

    def _open_sync(self) -> sqlite3.Connection:
        if self._path != Path(':memory:'):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        connection.execute('PRAGMA busy_timeout = 5000;')
        if self._path != Path(':memory:'):
            connection.execute('PRAGMA journal_mode = WAL;')
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                idempotency_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                lease_expires_at REAL NOT NULL,
                message_id INTEGER NULL,
                updated_at REAL NOT NULL
            );
        """)
        return connection

    def _claim_sync(self, connection: sqlite3.Connection, idempotency_key: str) -> DeliveryClaim:
        now = time.time()
        connection.execute('BEGIN IMMEDIATE;')
        try:
            row = connection.execute(
                """
                SELECT status, lease_expires_at, message_id
                FROM notification_deliveries
                WHERE idempotency_key = ?;
                """,
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                status, lease_expires_at, message_id = row
                if status == 'delivered':
                    connection.execute('COMMIT;')
                    return DeliveryClaim(
                        DeliveryClaimStatus.DELIVERED,
                        message_id=int(message_id) if message_id is not None else None,
                    )
                if status == 'processing' and float(lease_expires_at) > now:
                    connection.execute('COMMIT;')
                    return DeliveryClaim(DeliveryClaimStatus.PROCESSING)

            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    idempotency_key,
                    status,
                    lease_expires_at,
                    message_id,
                    updated_at
                )
                VALUES (?, 'processing', ?, NULL, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    status = 'processing',
                    lease_expires_at = excluded.lease_expires_at,
                    message_id = NULL,
                    updated_at = excluded.updated_at;
                """,
                (idempotency_key, now + self._lease_seconds, now),
            )
            connection.execute('COMMIT;')
        except Exception:
            connection.execute('ROLLBACK;')
            raise
        return DeliveryClaim(DeliveryClaimStatus.ACQUIRED)

    @staticmethod
    def _mark_delivered_sync(
        connection: sqlite3.Connection,
        idempotency_key: str,
        message_id: int | None,
    ) -> None:
        now = time.time()
        connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'delivered',
                lease_expires_at = 0,
                message_id = ?,
                updated_at = ?
            WHERE idempotency_key = ?;
            """,
            (message_id, now, idempotency_key),
        )

    @staticmethod
    def _release_sync(connection: sqlite3.Connection, idempotency_key: str) -> None:
        connection.execute(
            """
            UPDATE notification_deliveries
            SET status = 'retryable_failed',
                lease_expires_at = 0,
                updated_at = ?
            WHERE idempotency_key = ?;
            """,
            (time.time(), idempotency_key),
        )
