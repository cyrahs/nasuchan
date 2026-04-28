from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault, FSInputFile

import nasuchan.bot.delivery as delivery_module
from nasuchan.bot.app import create_runtime, perform_startup_healthcheck, register_bot_commands
from nasuchan.bot.delivery import send_markdown_to_chat
from nasuchan.clients import BackendApiTransportError, HealthStatus
from nasuchan.config.settings import AppConfig
from nasuchan.services import BackendCommandService


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


def build_delivery_bot(*, message_id: int = 456, photo_message_id: int = 789) -> SimpleNamespace:
    return SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=message_id)),
        send_photo=AsyncMock(return_value=SimpleNamespace(message_id=photo_message_id)),
        pin_chat_message=AsyncMock(),
    )


def telegram_bad_request(message: str = 'bad image') -> TelegramBadRequest:
    return TelegramBadRequest(method=SendPhoto(chat_id=123456789, photo='https://example.com/poster.jpg'), message=message)


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

    logger = SimpleNamespace(info=Mock(), warning=Mock())

    await perform_startup_healthcheck(
        BackendCommandService(fav_client=FailingBackendClient()),
        logger=logger,
    )

    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_startup_healthcheck_logs_success() -> None:
    status = HealthStatus(status='ok', generated_at='2026-03-08T12:00:00Z')

    class HealthyBackendClient:
        async def health(self) -> HealthStatus:
            return status

    logger = SimpleNamespace(info=Mock(), warning=Mock())

    await perform_startup_healthcheck(
        BackendCommandService(fav_client=HealthyBackendClient()),
        logger=logger,
    )

    logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_startup_healthcheck_skips_when_no_backends_are_configured() -> None:
    logger = SimpleNamespace(info=Mock(), warning=Mock())

    await perform_startup_healthcheck(BackendCommandService(), logger=logger)

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
    bot = build_delivery_bot()

    await send_markdown_to_chat(bot, 123456789, '*Hello*')

    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is True
    assert bot.send_message.await_args.kwargs['disable_notification'] is False
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_message.await_args.args == (123456789, '*Hello*')


@pytest.mark.asyncio
async def test_send_markdown_to_chat_uses_photo_caption_when_image_url_is_present() -> None:
    bot = build_delivery_bot()

    await send_markdown_to_chat(bot, 123456789, '*Hello*', image_url='https://example.com/poster.jpg')

    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert bot.send_photo.await_args.kwargs['caption'] == '*Hello*'
    assert bot.send_photo.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_photo.await_args.kwargs['disable_notification'] is False


@pytest.mark.asyncio
async def test_send_markdown_to_chat_uploads_downloaded_image_when_url_photo_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bot = build_delivery_bot()
    bot.send_photo = AsyncMock(side_effect=[telegram_bad_request(), SimpleNamespace(message_id=789)])
    image_path = tmp_path / 'poster.jpg'
    image_path.write_bytes(b'image')

    async def fake_download(_image_url: str) -> SimpleNamespace:
        return SimpleNamespace(path=image_path, filename='poster.jpg')

    monkeypatch.setattr(delivery_module, '_download_image_to_temp_file', fake_download)

    await send_markdown_to_chat(
        bot,
        123456789,
        '*Hello*',
        image_url='https://example.com/poster.jpg',
        disable_notification=True,
        pin=True,
    )

    assert bot.send_photo.await_count == 2
    assert bot.send_photo.await_args_list[0].args == (123456789, 'https://example.com/poster.jpg')
    uploaded_photo = bot.send_photo.await_args_list[1].args[1]
    assert isinstance(uploaded_photo, FSInputFile)
    assert uploaded_photo.filename == 'poster.jpg'
    assert bot.send_photo.await_args_list[1].kwargs['caption'] == '*Hello*'
    assert bot.send_photo.await_args_list[1].kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_photo.await_args_list[1].kwargs['disable_notification'] is True
    assert not image_path.exists()
    bot.send_message.assert_not_awaited()
    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=789,
        disable_notification=True,
    )


@pytest.mark.asyncio
async def test_send_markdown_to_chat_falls_back_to_text_when_local_image_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bot = build_delivery_bot(message_id=456)
    bot.send_photo = AsyncMock(side_effect=[telegram_bad_request(), telegram_bad_request('bad local upload')])
    image_path = tmp_path / 'poster.jpg'
    image_path.write_bytes(b'image')

    async def fake_download(_image_url: str) -> SimpleNamespace:
        return SimpleNamespace(path=image_path, filename='poster.jpg')

    monkeypatch.setattr(delivery_module, '_download_image_to_temp_file', fake_download)

    await send_markdown_to_chat(bot, 123456789, '*Hello*', image_url='https://example.com/poster.jpg', pin=True)

    assert bot.send_photo.await_count == 2
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args == (123456789, '*Hello*\n\n图片发送失败, 已改为纯文本通知')
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert not image_path.exists()
    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=456,
        disable_notification=False,
    )


@pytest.mark.asyncio
async def test_send_markdown_to_chat_sends_separate_image_failure_notice_for_max_length_text(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = build_delivery_bot(message_id=456)
    bot.send_photo = AsyncMock(side_effect=telegram_bad_request())
    markdown = 'x' * 4096

    async def fail_download(_image_url: str) -> None:
        raise delivery_module._ImageDownloadError('bad download')  # noqa: SLF001

    monkeypatch.setattr(delivery_module, '_download_image_to_temp_file', fail_download)

    await send_markdown_to_chat(bot, 123456789, markdown, image_url='https://example.com/poster.jpg')

    bot.send_photo.assert_awaited_once()
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[0].args == (123456789, markdown)
    assert bot.send_message.await_args_list[1].args == (123456789, '图片发送失败, 已改为纯文本通知')
    bot.pin_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_markdown_to_chat_does_not_fallback_for_non_bad_request_image_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = build_delivery_bot()
    bot.send_photo = AsyncMock(side_effect=RuntimeError('network uncertain'))

    async def fail_if_called(_image_url: str) -> None:
        pytest.fail('unexpected local image download')

    monkeypatch.setattr(delivery_module, '_download_image_to_temp_file', fail_if_called)

    with pytest.raises(RuntimeError, match='network uncertain'):
        await send_markdown_to_chat(bot, 123456789, '*Hello*', image_url='https://example.com/poster.jpg')

    bot.send_message.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_image_to_temp_file_rejects_local_addresses() -> None:
    with pytest.raises(delivery_module._ImageDownloadError, match='non-public address'):  # noqa: SLF001
        await delivery_module._download_image_to_temp_file('http://127.0.0.1/poster.jpg')  # noqa: SLF001


@pytest.mark.asyncio
async def test_send_markdown_to_chat_falls_back_to_photo_then_message_for_long_caption() -> None:
    bot = build_delivery_bot()
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
    bot.pin_chat_message.assert_not_awaited()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert 'caption' not in bot.send_photo.await_args.kwargs
    assert bot.send_photo.await_args.kwargs['disable_notification'] is True
    assert bot.send_message.await_args.args == (123456789, markdown)
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is False
    assert bot.send_message.await_args.kwargs['disable_notification'] is True
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'


@pytest.mark.asyncio
async def test_send_markdown_to_chat_pins_text_message_when_requested() -> None:
    bot = build_delivery_bot(message_id=456)

    await send_markdown_to_chat(bot, 123456789, '*Hello*', disable_notification=True, pin=True)

    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=456,
        disable_notification=True,
    )


@pytest.mark.asyncio
async def test_send_markdown_to_chat_pins_photo_message_when_requested() -> None:
    bot = build_delivery_bot(photo_message_id=789)

    await send_markdown_to_chat(
        bot,
        123456789,
        '*Hello*',
        image_url='https://example.com/poster.jpg',
        pin=True,
    )

    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=789,
        disable_notification=False,
    )


@pytest.mark.asyncio
async def test_send_markdown_to_chat_pins_long_caption_fallback_text_message() -> None:
    bot = build_delivery_bot(message_id=456, photo_message_id=789)

    await send_markdown_to_chat(
        bot,
        123456789,
        'x' * 1025,
        image_url='https://example.com/poster.jpg',
        pin=True,
    )

    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=456,
        disable_notification=False,
    )


@pytest.mark.asyncio
async def test_register_bot_commands_does_not_include_notifications_command() -> None:
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    logger = SimpleNamespace(info=Mock())

    await register_bot_commands(bot, 123456789, logger)

    commands = bot.set_my_commands.await_args_list[0].args[0]
    assert all(command.command != 'notifications' for command in commands)
