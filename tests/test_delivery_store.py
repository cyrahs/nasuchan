from __future__ import annotations

from collections import deque
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any

import pytest

from nasuchan.api.delivery_store import DeliveryClaimStatus, NotificationDeliveryStore


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class FakeConnection:
    def __init__(
        self,
        *,
        claim_rows: list[dict[str, Any] | None],
        selected_rows: list[dict[str, Any] | None],
    ) -> None:
        self._claim_rows = deque(claim_rows)
        self._selected_rows = deque(selected_rows)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> FakeCursor:
        self.calls.append((sql, params))
        if 'RETURNING status, message_id' in sql:
            return FakeCursor(self._claim_rows.popleft())
        if 'SELECT status, message_id' in sql:
            return FakeCursor(self._selected_rows.popleft())
        return FakeCursor()


class FakeConnectionContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.open_calls = 0
        self.close_calls = 0

    async def open(self, *, wait: bool) -> None:
        assert wait is True
        self.open_calls += 1

    def connection(self) -> FakeConnectionContext:
        return FakeConnectionContext(self._connection)

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_delivery_store_maps_acquired_processing_and_delivered_claims() -> None:
    connection = FakeConnection(
        claim_rows=[
            {'status': 'processing', 'message_id': None},
            None,
            None,
        ],
        selected_rows=[
            {'status': 'processing', 'message_id': None},
            {'status': 'delivered', 'message_id': 123},
        ],
    )
    pool = FakePool(connection)
    store = NotificationDeliveryStore('ignored', pool=pool)

    first_claim = await store.claim('fav:1')
    processing_claim = await store.claim('fav:1')
    delivered_claim = await store.claim('fav:1')
    await store.aclose()

    assert first_claim.status == DeliveryClaimStatus.ACQUIRED
    assert processing_claim.status == DeliveryClaimStatus.PROCESSING
    assert delivered_claim.status == DeliveryClaimStatus.DELIVERED
    assert delivered_claim.message_id == 123
    assert pool.open_calls == 1
    assert pool.close_calls == 1
    assert sum('CREATE TABLE IF NOT EXISTS' in sql for sql, _ in connection.calls) == 1


@pytest.mark.asyncio
async def test_delivery_store_marks_delivered_and_release_allows_retry() -> None:
    connection = FakeConnection(
        claim_rows=[
            {'status': 'processing', 'message_id': None},
            {'status': 'processing', 'message_id': None},
        ],
        selected_rows=[],
    )
    store = NotificationDeliveryStore('ignored', pool=FakePool(connection))

    first_claim = await store.claim('fav:2')
    await store.release('fav:2')
    retry_claim = await store.claim('fav:2')
    await store.mark_delivered('fav:2', message_id=456)
    await store.aclose()

    assert first_claim.status == DeliveryClaimStatus.ACQUIRED
    assert retry_claim.status == DeliveryClaimStatus.ACQUIRED
    assert any("SET status = 'retryable_failed'" in sql for sql, _ in connection.calls)
    assert any("SET status = 'delivered'" in sql and params == (456, 'fav:2') for sql, params in connection.calls)


@pytest.mark.asyncio
async def test_delivery_store_raises_if_conflicting_claim_disappears() -> None:
    connection = FakeConnection(claim_rows=[None], selected_rows=[None])
    store = NotificationDeliveryStore('ignored', pool=FakePool(connection))

    with pytest.raises(RuntimeError, match='claim disappeared'):
        await store.claim('fav:3')

    await store.aclose()
