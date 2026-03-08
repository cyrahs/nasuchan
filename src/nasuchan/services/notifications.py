from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nasuchan.clients import FavBackendClient, NotificationRecord

NotificationSender = Callable[[NotificationRecord], Awaitable[None]]


@dataclass(slots=True)
class DeliveryReport:
    fetched: int
    delivered: int
    failed: int
    acked: int
    delivered_ids: list[int]
    failed_ids: list[int]


class NotificationDeliveryService:
    def __init__(
        self,
        backend_client: FavBackendClient,
        *,
        batch_limit: int,
        sender: NotificationSender,
        logger: logging.Logger | None = None,
    ) -> None:
        self._backend_client = backend_client
        self._batch_limit = batch_limit
        self._sender = sender
        self._logger = logger or logging.getLogger(__name__)
        self._lock = asyncio.Lock()

    async def deliver_once(self) -> DeliveryReport:
        async with self._lock:
            notifications = await self._backend_client.list_notifications(status='unread', limit=self._batch_limit)
            delivered_ids: list[int] = []
            failed_ids: list[int] = []
            for notification in notifications:
                if await self._deliver_notification(notification):
                    delivered_ids.append(notification.id)
                else:
                    failed_ids.append(notification.id)
            acked = 0
            if delivered_ids:
                acked = await self._backend_client.ack_notifications(delivered_ids)
            return DeliveryReport(
                fetched=len(notifications),
                delivered=len(delivered_ids),
                failed=len(failed_ids),
                acked=acked,
                delivered_ids=delivered_ids,
                failed_ids=failed_ids,
            )

    async def _deliver_notification(self, notification: NotificationRecord) -> bool:
        try:
            await self._sender(notification)
        except Exception:
            self._logger.exception('Failed to deliver notification %s', notification.id)
            return False
        return True


class NotificationWorker:
    def __init__(
        self,
        delivery_service: NotificationDeliveryService,
        *,
        interval_seconds: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self._delivery_service = delivery_service
        self._interval_seconds = interval_seconds
        self._logger = logger or logging.getLogger(__name__)
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._delivery_service.deliver_once()
            except Exception:
                self._logger.exception('Background notification delivery failed')
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        self._stop_event.set()
