from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nasuchan.bot.app import create_runtime, perform_startup_healthcheck
from nasuchan.clients import BackendApiTransportError, HealthStatus
from nasuchan.config.settings import AppConfig


def build_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
            'backend_api': {'base_url': 'https://fav.example.com', 'token': 'shared-token', 'request_timeout_seconds': 15},
            'polling': {
                'control_poll_interval_seconds': 2,
                'control_poll_timeout_seconds': 600,
                'notification_poll_interval_seconds': 5,
                'notification_batch_limit': 50,
            },
            'logging': {'level': 'INFO'},
        }
    )


class FakeBackendClient:
    async def list_notifications(self, *, _status: str, _limit: int) -> list[object]:
        return []

    async def ack_notifications(self, ids: list[int]) -> int:
        return len(ids)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_create_runtime_wires_dispatcher_and_services() -> None:
    fake_bot = SimpleNamespace(send_message=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    runtime = create_runtime(build_config(), bot=fake_bot, backend_client=FakeBackendClient())

    assert runtime.dispatcher is not None
    assert runtime.notification_service is not None
    assert runtime.notification_worker is not None

    await runtime.aclose()


@pytest.mark.asyncio
async def test_startup_healthcheck_is_non_fatal_when_backend_is_unavailable() -> None:
    class FailingBackendClient:
        async def health(self) -> HealthStatus:
            raise BackendApiTransportError('boom')

    await perform_startup_healthcheck(FailingBackendClient(), logger=SimpleNamespace(exception=lambda *args, **kwargs: None))


@pytest.mark.asyncio
async def test_startup_healthcheck_logs_success() -> None:
    status = HealthStatus(status='ok', generated_at='2026-03-08T12:00:00Z')

    class HealthyBackendClient:
        async def health(self) -> HealthStatus:
            return status

    logger = SimpleNamespace(info=Mock(), exception=Mock())

    await perform_startup_healthcheck(HealthyBackendClient(), logger=logger)

    logger.info.assert_called_once()
