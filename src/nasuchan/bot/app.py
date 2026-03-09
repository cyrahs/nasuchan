from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat, BotCommandScopeDefault

from nasuchan.clients import BackendApiError, FavBackendClient
from nasuchan.config import AppConfig, load_config

from .handlers import build_commands_router, build_hanime1_router
from .middleware import AdminChatMiddleware

_BOT_COMMANDS = [
    BotCommand(command='start', description='Show help'),
    BotCommand(command='health', description='Check backend health'),
    BotCommand(command='jobs', description='List backend jobs'),
    BotCommand(command='run', description='Trigger a backend job'),
    BotCommand(command='config', description='Manage runtime configuration'),
    BotCommand(command='cancel', description='Clear the active bot state'),
]


@dataclass(slots=True)
class BotRuntime:
    bot: Bot
    dispatcher: Dispatcher
    backend_client: FavBackendClient
    http_client: httpx.AsyncClient | None = None
    manage_resources: bool = True

    async def aclose(self) -> None:
        if not self.manage_resources:
            return
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
    manage_resources: bool = True,
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

    dispatcher.include_router(
        build_commands_router(
            runtime_backend_client,
            config.polling,
        )
    )
    dispatcher.include_router(build_hanime1_router(runtime_backend_client))

    return BotRuntime(
        bot=runtime_bot,
        dispatcher=dispatcher,
        backend_client=runtime_backend_client,
        http_client=runtime_http_client,
        manage_resources=manage_resources,
    )


async def run_polling(config_path: Path = Path('./config.toml')) -> None:
    config = load_config(config_path)
    configure_logging(config)
    logger = logging.getLogger(__name__)
    runtime = create_runtime(config)

    try:
        await perform_startup_healthcheck(runtime.backend_client, logger)
        await register_bot_commands(runtime.bot, config.telegram.admin_chat_id, logger)
        await runtime.dispatcher.start_polling(runtime.bot)
    finally:
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
