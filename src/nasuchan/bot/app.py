from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

from nasuchan.clients import BackendApiError, FavBackendClient, NotificationRecord
from nasuchan.config import AppConfig, load_config
from nasuchan.services import NotificationDeliveryService, NotificationWorker, format_notification_html

from .handlers import build_commands_router, build_hanime1_router
from .middleware import AdminChatMiddleware

_BOT_COMMANDS = [
    BotCommand(command='start', description='Show help'),
    BotCommand(command='health', description='Check backend health'),
    BotCommand(command='jobs', description='List backend jobs'),
    BotCommand(command='run', description='Trigger a backend job'),
    BotCommand(command='config', description='Manage runtime configuration'),
    BotCommand(command='notifications', description='Force one notification delivery cycle'),
    BotCommand(command='cancel', description='Clear the active bot state'),
]


@dataclass(slots=True)
class BotRuntime:
    bot: Bot
    dispatcher: Dispatcher
    backend_client: FavBackendClient
    notification_service: NotificationDeliveryService
    notification_worker: NotificationWorker
    http_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        self.notification_worker.stop()
        if self.http_client is not None:
            await self.http_client.aclose()
        else:
            await self.backend_client.aclose()
        await self.bot.session.close()


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.logging.level, logging.INFO),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )


def create_runtime(
    config: AppConfig,
    *,
    bot: Bot | None = None,
    backend_client: FavBackendClient | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> BotRuntime:
    runtime_http_client = http_client
    runtime_backend_client = backend_client
    if runtime_backend_client is None:
        fav_backend = config.backend.fav
        runtime_http_client = runtime_http_client or httpx.AsyncClient(
            base_url=fav_backend.base_url,
            timeout=fav_backend.request_timeout_seconds,
            follow_redirects=False,
        )
        runtime_backend_client = FavBackendClient(fav_backend, client=runtime_http_client)

    runtime_bot = bot or Bot(token=config.telegram.bot_token)
    dispatcher = Dispatcher(storage=MemoryStorage())

    admin_middleware = AdminChatMiddleware(config.telegram.admin_chat_id)
    dispatcher.message.outer_middleware(admin_middleware)
    dispatcher.callback_query.outer_middleware(admin_middleware)

    notification_service = NotificationDeliveryService(
        runtime_backend_client,
        batch_limit=config.polling.notification_batch_limit,
        sender=lambda notification: send_notification_to_chat(
            runtime_bot,
            config.telegram.admin_chat_id,
            notification,
            logging.getLogger(__name__),
        ),
    )
    dispatcher.include_router(
        build_commands_router(
            runtime_backend_client,
            config.polling,
            notification_service,
        )
    )
    dispatcher.include_router(build_hanime1_router(runtime_backend_client))

    notification_worker = NotificationWorker(
        notification_service,
        interval_seconds=config.polling.notification_poll_interval_seconds,
    )
    return BotRuntime(
        bot=runtime_bot,
        dispatcher=dispatcher,
        backend_client=runtime_backend_client,
        notification_service=notification_service,
        notification_worker=notification_worker,
        http_client=runtime_http_client,
    )


async def run_polling(config_path: Path = Path('./config.toml')) -> None:
    config = load_config(config_path)
    configure_logging(config)
    logger = logging.getLogger(__name__)
    runtime = create_runtime(config)
    worker_task: asyncio.Task[None] | None = None

    try:
        await perform_startup_healthcheck(runtime.backend_client, logger)
        await register_bot_commands(runtime.bot, config.telegram.admin_chat_id, logger)
        worker_task = asyncio.create_task(runtime.notification_worker.run(), name='notification-worker')
        await runtime.dispatcher.start_polling(runtime.bot)
    finally:
        runtime.notification_worker.stop()
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
        await runtime.aclose()


async def perform_startup_healthcheck(backend_client: FavBackendClient, logger: logging.Logger) -> None:
    try:
        status = await backend_client.health()
    except BackendApiError:
        logger.exception('Startup backend health check failed')
        return
    logger.info('Startup backend health check succeeded with status=%s', status.status)


async def register_bot_commands(bot: Bot, admin_chat_id: int, logger: logging.Logger) -> None:
    scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeChat(chat_id=admin_chat_id),
    ]
    for scope in scopes:
        await bot.set_my_commands(_BOT_COMMANDS, scope=scope)
    logger.info('Registered bot commands for default, private, and admin chat scopes')


async def send_notification_to_chat(
    bot: Bot,
    chat_id: int,
    notification: NotificationRecord,
    logger: logging.Logger,
) -> None:
    content = format_notification_html(notification)
    if content.image_url:
        photo_sent = await _try_send_notification_photo(bot, chat_id, content.html, content.image_url, logger)
        if photo_sent and len(content.html) <= 1024:
            return
    await bot.send_message(
        chat_id,
        content.html,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _try_send_notification_photo(
    bot: Bot,
    chat_id: int,
    html: str,
    image_url: str,
    logger: logging.Logger,
) -> bool:
    try:
        if len(html) <= 1024:
            await bot.send_photo(
                chat_id,
                photo=image_url,
                caption=html,
                parse_mode=ParseMode.HTML,
            )
        else:
            await bot.send_photo(chat_id, photo=image_url)
    except Exception:
        logger.exception('Failed to send notification image, falling back to text')
        return False
    return True
