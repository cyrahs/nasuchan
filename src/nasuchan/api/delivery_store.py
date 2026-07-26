from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_ENSURE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notification_deliveries (
    idempotency_key TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('processing', 'delivered', 'retryable_failed')),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    message_id BIGINT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
"""

_CLAIM_SQL = """
INSERT INTO notification_deliveries (
    idempotency_key,
    status,
    lease_expires_at,
    message_id,
    updated_at
)
VALUES (
    %s,
    'processing',
    CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
    NULL,
    CURRENT_TIMESTAMP
)
ON CONFLICT (idempotency_key) DO UPDATE SET
    status = 'processing',
    lease_expires_at = EXCLUDED.lease_expires_at,
    message_id = NULL,
    updated_at = EXCLUDED.updated_at
WHERE notification_deliveries.status <> 'delivered'
  AND (
      notification_deliveries.status <> 'processing'
      OR notification_deliveries.lease_expires_at <= CURRENT_TIMESTAMP
  )
RETURNING status, message_id;
"""

_SELECT_CLAIM_SQL = """
SELECT status, message_id
FROM notification_deliveries
WHERE idempotency_key = %s;
"""

_MARK_DELIVERED_SQL = """
UPDATE notification_deliveries
SET status = 'delivered',
    lease_expires_at = CURRENT_TIMESTAMP,
    message_id = %s,
    updated_at = CURRENT_TIMESTAMP
WHERE idempotency_key = %s;
"""

_RELEASE_SQL = """
UPDATE notification_deliveries
SET status = 'retryable_failed',
    lease_expires_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE idempotency_key = %s;
"""


class DeliveryClaimStatus(StrEnum):
    ACQUIRED = 'acquired'
    DELIVERED = 'delivered'
    PROCESSING = 'processing'


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    status: DeliveryClaimStatus
    message_id: int | None = None


class NotificationDeliveryStore:
    def __init__(
        self,
        conninfo: str,
        *,
        lease_seconds: float = 120,
        pool: Any | None = None,
    ) -> None:
        self._lease_seconds = lease_seconds
        self._pool = pool or AsyncConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={'row_factory': dict_row},
        )
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def open(self) -> None:
        await self._ensure_ready()

    async def claim(self, idempotency_key: str) -> DeliveryClaim:
        await self._ensure_ready()
        async with self._pool.connection() as connection:
            cursor = await connection.execute(_CLAIM_SQL, (idempotency_key, self._lease_seconds))
            claimed_row = await cursor.fetchone()
            if claimed_row is not None:
                return DeliveryClaim(DeliveryClaimStatus.ACQUIRED)

            cursor = await connection.execute(_SELECT_CLAIM_SQL, (idempotency_key,))
            existing_row = await cursor.fetchone()

        if existing_row is None:
            msg = 'Notification delivery claim disappeared after a conflicting update'
            raise RuntimeError(msg)
        if existing_row['status'] == 'delivered':
            message_id = existing_row['message_id']
            return DeliveryClaim(
                DeliveryClaimStatus.DELIVERED,
                message_id=int(message_id) if message_id is not None else None,
            )
        return DeliveryClaim(DeliveryClaimStatus.PROCESSING)

    async def mark_delivered(self, idempotency_key: str, *, message_id: int | None) -> None:
        await self._ensure_ready()
        async with self._pool.connection() as connection:
            await connection.execute(_MARK_DELIVERED_SQL, (message_id, idempotency_key))

    async def release(self, idempotency_key: str) -> None:
        await self._ensure_ready()
        async with self._pool.connection() as connection:
            await connection.execute(_RELEASE_SQL, (idempotency_key,))

    async def aclose(self) -> None:
        await self._pool.close()
        self._ready = False

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            await self._pool.open(wait=True)
            async with self._pool.connection() as connection:
                await connection.execute(_ENSURE_SCHEMA_SQL)
            self._ready = True
