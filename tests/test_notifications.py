from __future__ import annotations

import asyncio

import pytest

from nasuchan.clients import NotificationRecord
from nasuchan.services.notifications import NotificationDeliveryService


def build_notification(notification_id: int, title: str) -> NotificationRecord:
    return NotificationRecord(
        id=notification_id,
        kind='download_completed',
        source='bilibili',
        title=title,
        body='body',
        link_url='',
        image_url='',
        payload={},
        status='unread',
        created_at='2026-03-08T12:00:00Z',
        read_at=None,
    )


class FakeBackendClient:
    def __init__(self, notifications: list[NotificationRecord]) -> None:
        self.notifications = notifications
        self.acked_ids: list[int] = []
        self.list_calls = 0
        self.block_event = asyncio.Event()
        self.should_block = False

    async def list_notifications(self, *, status: str, limit: int) -> list[NotificationRecord]:
        assert status == 'unread'
        assert limit > 0
        self.list_calls += 1
        if self.should_block:
            await self.block_event.wait()
        return self.notifications

    async def ack_notifications(self, ids: list[int]) -> int:
        self.acked_ids.extend(ids)
        return len(ids)


@pytest.mark.asyncio
async def test_notification_delivery_acks_only_successful_ids() -> None:
    backend_client = FakeBackendClient(
        [
            build_notification(1, 'First'),
            build_notification(2, 'Second'),
        ]
    )
    sent_messages: list[str] = []

    async def sender(text: str) -> None:
        sent_messages.append(text)
        if 'Second' in text:
            raise RuntimeError('send failed')

    service = NotificationDeliveryService(backend_client, batch_limit=50, sender=sender)
    report = await service.deliver_once()

    assert sent_messages[0].startswith('[download_completed] First')
    assert backend_client.acked_ids == [1]
    assert report.delivered_ids == [1]
    assert report.failed_ids == [2]


@pytest.mark.asyncio
async def test_notification_delivery_preserves_backend_order() -> None:
    backend_client = FakeBackendClient(
        [
            build_notification(1, 'First'),
            build_notification(2, 'Second'),
            build_notification(3, 'Third'),
        ]
    )
    sent_messages: list[str] = []

    async def sender(text: str) -> None:
        sent_messages.append(text)

    service = NotificationDeliveryService(backend_client, batch_limit=50, sender=sender)
    await service.deliver_once()

    assert [text.split('] ', maxsplit=1)[1].splitlines()[0] for text in sent_messages] == ['First', 'Second', 'Third']


@pytest.mark.asyncio
async def test_manual_and_background_delivery_do_not_overlap() -> None:
    backend_client = FakeBackendClient([build_notification(1, 'First')])
    backend_client.should_block = True

    async def sender(_text: str) -> None:
        return None

    service = NotificationDeliveryService(backend_client, batch_limit=50, sender=sender)
    first_task = asyncio.create_task(service.deliver_once())

    await asyncio.sleep(0.05)
    second_task = asyncio.create_task(service.deliver_once())
    await asyncio.sleep(0.05)
    assert backend_client.list_calls == 1

    backend_client.block_event.set()
    await first_task
    await second_task
    assert backend_client.list_calls == 2
