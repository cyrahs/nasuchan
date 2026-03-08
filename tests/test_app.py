from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

from nasuchan.bot.app import create_runtime, perform_startup_healthcheck, register_bot_commands, send_notification_to_chat
from nasuchan.clients import BackendApiTransportError, HealthStatus, NotificationRecord
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
    fake_bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
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


@pytest.mark.asyncio
async def test_register_bot_commands_sets_expected_scopes() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    logger = SimpleNamespace(info=Mock())

    await register_bot_commands(bot, 123456789, logger)

    assert bot.set_my_commands.await_count == 3
    scopes = [call.kwargs['scope'] for call in bot.set_my_commands.await_args_list]
    assert isinstance(scopes[0], BotCommandScopeDefault)
    assert isinstance(scopes[1], BotCommandScopeAllPrivateChats)
    assert isinstance(scopes[2], BotCommandScopeChat)
    assert scopes[2].chat_id == 123456789
    logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_send_notification_to_chat_uses_html_message_without_preview() -> None:
    notification = NotificationRecord(
        id=1,
        kind='download_completed',
        source='bilibili',
        title='Example Title',
        body='Example Body',
        link_url='https://example.com/watch',
        image_url='',
        payload={},
        status='unread',
        created_at='2026-03-08T12:00:00Z',
        read_at=None,
    )
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    logger = SimpleNamespace(exception=Mock())

    await send_notification_to_chat(bot, 123456789, notification, logger)

    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is True
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'HTML'
    assert (
        bot.send_message.await_args.args[1]
        == 'download_completed\n<b>Example Title</b>\nExample Body\n<a href="https://example.com/watch">链接</a>'
    )


@pytest.mark.asyncio
async def test_send_notification_to_chat_uses_photo_when_image_url_exists() -> None:
    notification = NotificationRecord(
        id=1,
        kind='download_completed',
        source='bilibili',
        title='Example Title',
        body='Example Body',
        link_url='https://example.com/watch',
        image_url='https://example.com/image.jpg',
        payload={},
        status='unread',
        created_at='2026-03-08T12:00:00Z',
        read_at=None,
    )
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    logger = SimpleNamespace(exception=Mock())

    await send_notification_to_chat(bot, 123456789, notification, logger)

    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
