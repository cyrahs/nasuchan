from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from aiogram import Bot
from aiohttp import web

from nasuchan.api import create_app
from nasuchan.api.server import PublicApiServer
from nasuchan.bot.app import BotRuntime, create_runtime, perform_startup_healthcheck, register_bot_commands
from nasuchan.clients import AninamerClient, FavBackendClient
from nasuchan.config import AppConfig, PublicApiSettings, load_config

_DEFAULT_CONFIG_PATH = Path('./config.toml')
_LOGGER = logging.getLogger(__name__)


class ApiAppFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        bot: Bot | None = None,
        backend_client: FavBackendClient | None = None,
        http_client: httpx.AsyncClient | None = None,
        manage_resources: bool = True,
    ) -> web.Application: ...


class BotRuntimeFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        bot: Bot | None = None,
        backend_client: FavBackendClient | None = None,
        aninamer_client: AninamerClient | None = None,
        http_client: httpx.AsyncClient | None = None,
        aninamer_http_client: httpx.AsyncClient | None = None,
        manage_resources: bool = True,
    ) -> BotRuntime: ...


class ApiServerFactory(Protocol):
    def __call__(self, app: web.Application, *, host: str, port: int) -> PublicApiServer: ...


@dataclass(slots=True)
class CombinedResources:
    bot: Bot
    backend_client: FavBackendClient | None = None
    aninamer_client: AninamerClient | None = None
    http_client: httpx.AsyncClient | None = None
    aninamer_http_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
        elif self.backend_client is not None:
            await self.backend_client.aclose()
        if self.aninamer_http_client is not None:
            await self.aninamer_http_client.aclose()
        elif self.aninamer_client is not None:
            await self.aninamer_client.aclose()
        await self.bot.session.close()


@dataclass(slots=True)
class CombinedRuntime:
    resources: CombinedResources
    bot_runtime: BotRuntime
    api_server: PublicApiServer

    async def aclose(self) -> None:
        await self.api_server.stop()
        await self.resources.aclose()


def configure_logging(config: AppConfig) -> None:
    logging.basicConfig(
        level=getattr(logging, config.logging.level, logging.INFO),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )


def create_combined_runtime(
    config: AppConfig,
    *,
    bot: Bot | None = None,
    http_client: httpx.AsyncClient | None = None,
    backend_client: FavBackendClient | None = None,
    aninamer_http_client: httpx.AsyncClient | None = None,
    aninamer_client: AninamerClient | None = None,
    api_app_factory: ApiAppFactory = create_app,
    bot_runtime_factory: BotRuntimeFactory = create_runtime,
    api_server_factory: ApiServerFactory = PublicApiServer,
) -> CombinedRuntime:
    public_api = _require_public_api_config(config)
    shared_bot = bot or Bot(token=config.telegram.bot_token)
    fav_backend = config.backend.fav
    shared_http_client = http_client
    shared_backend_client = backend_client
    if shared_backend_client is None and fav_backend is not None:
        shared_http_client = shared_http_client or httpx.AsyncClient(
            base_url=fav_backend.base_url,
            timeout=fav_backend.request_timeout_seconds,
            follow_redirects=False,
        )
        shared_backend_client = FavBackendClient(fav_backend, client=shared_http_client)

    aninamer_backend = config.backend.aninamer
    shared_aninamer_http_client = aninamer_http_client
    shared_aninamer_client = aninamer_client
    if shared_aninamer_client is None and aninamer_backend is not None:
        shared_aninamer_http_client = shared_aninamer_http_client or httpx.AsyncClient(
            base_url=aninamer_backend.base_url,
            timeout=aninamer_backend.request_timeout_seconds,
            follow_redirects=False,
        )
        shared_aninamer_client = AninamerClient(aninamer_backend, client=shared_aninamer_http_client)

    resources = CombinedResources(
        bot=shared_bot,
        backend_client=shared_backend_client,
        aninamer_client=shared_aninamer_client,
        http_client=shared_http_client,
        aninamer_http_client=shared_aninamer_http_client,
    )
    bot_runtime = bot_runtime_factory(
        config,
        bot=shared_bot,
        backend_client=shared_backend_client,
        aninamer_client=shared_aninamer_client,
        http_client=shared_http_client,
        aninamer_http_client=shared_aninamer_http_client,
        manage_resources=False,
    )
    api_app = api_app_factory(
        config,
        bot=shared_bot,
        backend_client=shared_backend_client,
        http_client=shared_http_client,
        manage_resources=False,
    )
    api_server = api_server_factory(api_app, host=public_api.bind, port=public_api.port)
    return CombinedRuntime(
        resources=resources,
        bot_runtime=bot_runtime,
        api_server=api_server,
    )


async def run_combined(config_path: Path = _DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    configure_logging(config)
    runtime = create_combined_runtime(config)
    try:
        await _run_runtime(config, runtime)
    finally:
        await runtime.aclose()


async def _run_runtime(config: AppConfig, runtime: CombinedRuntime) -> None:
    api_task = asyncio.create_task(runtime.api_server.run(), name='public-api')
    bot_task: asyncio.Task[None] | None = None
    try:
        await runtime.api_server.wait_started()
        await perform_startup_healthcheck(runtime.bot_runtime.command_service, _LOGGER)
        await register_bot_commands(runtime.bot_runtime.bot, config.telegram.admin_chat_id, _LOGGER)
        bot_task = asyncio.create_task(
            runtime.bot_runtime.dispatcher.start_polling(runtime.bot_runtime.bot),
            name='bot-polling',
        )
        await _wait_for_failure(bot_task, api_task)
    finally:
        if bot_task is not None:
            await _cancel_task(bot_task)
        await _cancel_task(api_task)


async def _wait_for_failure(*tasks: asyncio.Task[None]) -> None:
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    for task in done:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            raise exc
        msg = f'{task.get_name()} exited unexpectedly'
        raise RuntimeError(msg)


async def _cancel_task(task: asyncio.Task[None]) -> None:
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _require_public_api_config(config: AppConfig) -> PublicApiSettings:
    if config.public_api is None:
        msg = 'public_api configuration is required to run the combined runtime'
        raise ValueError(msg)
    return config.public_api
