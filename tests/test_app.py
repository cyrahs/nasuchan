from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

from nasuchan.bot.app import create_runtime, perform_startup_healthcheck, register_bot_commands
from nasuchan.bot.delivery import send_markdown_to_chat
from nasuchan.clients import BackendApiTransportError, HealthStatus
from nasuchan.config.settings import AppConfig


def build_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
            'backend': {'fav': {'base_url': 'https://fav.example.com', 'token': 'shared-token', 'request_timeout_seconds': 15}},
            'polling': {
                'control_poll_interval_seconds': 2,
                'control_poll_timeout_seconds': 600,
            },
            'logging': {'level': 'INFO'},
        }
    )


class FakeBackendClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_create_runtime_wires_dispatcher_and_services() -> None:
    fake_bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    backend_client = FakeBackendClient()
    runtime = create_runtime(build_config(), bot=fake_bot, backend_client=backend_client)

    assert runtime.dispatcher is not None
    assert runtime.backend_client is not None

    await runtime.aclose()
    assert backend_client.closed is True


@pytest.mark.asyncio
async def test_create_runtime_can_skip_resource_management() -> None:
    fake_bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock(), session=SimpleNamespace(close=AsyncMock()))
    backend_client = FakeBackendClient()
    runtime = create_runtime(
        build_config(),
        bot=fake_bot,
        backend_client=backend_client,
        manage_resources=False,
    )

    await runtime.aclose()

    assert backend_client.closed is False
    fake_bot.session.close.assert_not_awaited()


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
async def test_send_markdown_to_chat_uses_markdown_v2_and_default_flags() -> None:
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())

    await send_markdown_to_chat(bot, 123456789, '*Hello*')

    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_not_awaited()
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is True
    assert bot.send_message.await_args.kwargs['disable_notification'] is False
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_message.await_args.args == (123456789, '*Hello*')


@pytest.mark.asyncio
async def test_send_markdown_to_chat_uses_photo_caption_when_image_url_is_present() -> None:
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())

    await send_markdown_to_chat(bot, 123456789, '*Hello*', image_url='https://example.com/poster.jpg')

    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert bot.send_photo.await_args.kwargs['caption'] == '*Hello*'
    assert bot.send_photo.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_photo.await_args.kwargs['disable_notification'] is False


@pytest.mark.asyncio
async def test_send_markdown_to_chat_falls_back_to_photo_then_message_for_long_caption() -> None:
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    markdown = 'x' * 1025

    await send_markdown_to_chat(
        bot,
        123456789,
        markdown,
        image_url='https://example.com/poster.jpg',
        disable_web_page_preview=False,
        disable_notification=True,
    )

    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert 'caption' not in bot.send_photo.await_args.kwargs
    assert bot.send_photo.await_args.kwargs['disable_notification'] is True
    assert bot.send_message.await_args.args == (123456789, markdown)
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is False
    assert bot.send_message.await_args.kwargs['disable_notification'] is True
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'


@pytest.mark.asyncio
async def test_register_bot_commands_does_not_include_notifications_command() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    logger = SimpleNamespace(info=Mock())

    await register_bot_commands(bot, 123456789, logger)

    commands = bot.set_my_commands.await_args_list[0].args[0]
    assert all(command.command != 'notifications' for command in commands)
