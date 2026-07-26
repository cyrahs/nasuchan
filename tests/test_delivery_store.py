from __future__ import annotations

from pathlib import Path

import pytest

from nasuchan.api.delivery_store import DeliveryClaimStatus, NotificationDeliveryStore


@pytest.mark.asyncio
async def test_delivery_store_deduplicates_and_persists(tmp_path: Path) -> None:
    state_path = tmp_path / 'deliveries.sqlite3'
    store = NotificationDeliveryStore(state_path)

    first_claim = await store.claim('fav:1')
    processing_claim = await store.claim('fav:1')
    await store.mark_delivered('fav:1', message_id=123)
    delivered_claim = await store.claim('fav:1')
    await store.aclose()

    reopened_store = NotificationDeliveryStore(state_path)
    persisted_claim = await reopened_store.claim('fav:1')
    await reopened_store.aclose()

    assert first_claim.status == DeliveryClaimStatus.ACQUIRED
    assert processing_claim.status == DeliveryClaimStatus.PROCESSING
    assert delivered_claim.status == DeliveryClaimStatus.DELIVERED
    assert delivered_claim.message_id == 123
    assert persisted_claim.status == DeliveryClaimStatus.DELIVERED
    assert persisted_claim.message_id == 123


@pytest.mark.asyncio
async def test_delivery_store_release_allows_retry() -> None:
    store = NotificationDeliveryStore(Path(':memory:'))

    first_claim = await store.claim('fav:2')
    await store.release('fav:2')
    retry_claim = await store.claim('fav:2')
    await store.aclose()

    assert first_claim.status == DeliveryClaimStatus.ACQUIRED
    assert retry_claim.status == DeliveryClaimStatus.ACQUIRED
