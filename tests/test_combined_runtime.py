from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nasuchan.combined import create_combined_runtime, run_combined
from nasuchan.config.settings import AppConfig


def build_config(*, include_public_api: bool = True) -> AppConfig:
    raw_config = {
        'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
        'backend': {'fav': {'base_url': 'https://fav.example.com', 'token': 'shared-token', 'request_timeout_seconds': 15}},
        'polling': {
            'control_poll_interval_seconds': 2,
            'control_poll_timeout_seconds': 600,
        },
        'logging': {'level': 'INFO'},
    }
    if include_public_api:
        raw_config['public_api'] = {'bind': '127.0.0.1', 'port': 8092, 'token': 'public-runtime-api-token'}
    return AppConfig.model_validate(raw_config)


def build_bot() -> SimpleNamespace:
    return SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))


class FakeApiServer:
    def __init__(self, app: object, *, host: str, port: int) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.stop = AsyncMock()


def test_create_combined_runtime_wires_shared_resources() -> None:
    captured: dict[str, object] = {}
    fake_bot = build_bot()
    fake_http_client = SimpleNamespace(aclose=AsyncMock())
    fake_backend_client = SimpleNamespace(aclose=AsyncMock())

    def bot_runtime_factory(_config: AppConfig, **kwargs: object) -> SimpleNamespace:
        captured['bot_runtime'] = kwargs
        return SimpleNamespace(
            bot=kwargs['bot'],
            backend_client=kwargs['backend_client'],
            dispatcher=SimpleNamespace(start_polling=AsyncMock()),
        )

    def api_app_factory(_config: AppConfig, **kwargs: object) -> object:
        captured['api_app'] = kwargs
        return object()

    runtime = create_combined_runtime(
        build_config(),
        bot=fake_bot,
        http_client=fake_http_client,
        backend_client=fake_backend_client,
        api_app_factory=api_app_factory,
        bot_runtime_factory=bot_runtime_factory,
        api_server_factory=FakeApiServer,
    )

    assert runtime.resources.bot is fake_bot
    assert runtime.resources.http_client is fake_http_client
    assert runtime.resources.backend_client is fake_backend_client
    assert captured['bot_runtime'] == {
        'bot': fake_bot,
        'backend_client': fake_backend_client,
        'http_client': fake_http_client,
        'manage_resources': False,
    }
    assert captured['api_app'] == {
        'bot': fake_bot,
        'backend_client': fake_backend_client,
        'http_client': fake_http_client,
        'manage_resources': False,
    }
    assert runtime.api_server.host == '127.0.0.1'
    assert runtime.api_server.port == 8092


@pytest.mark.asyncio
async def test_combined_runtime_aclose_stops_server_and_closes_shared_resources() -> None:
    runtime = create_combined_runtime(
        build_config(),
        bot=build_bot(),
        http_client=SimpleNamespace(aclose=AsyncMock()),
        backend_client=SimpleNamespace(aclose=AsyncMock()),
        api_server_factory=FakeApiServer,
    )

    await runtime.aclose()

    runtime.api_server.stop.assert_awaited_once()
    runtime.resources.backend_client.aclose.assert_awaited_once()
    runtime.resources.http_client.aclose.assert_awaited_once()
    runtime.resources.bot.session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_combined_requires_public_api_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('nasuchan.combined.load_config', lambda path: build_config(include_public_api=False))
    monkeypatch.setattr('nasuchan.combined.configure_logging', lambda config: None)

    with pytest.raises(ValueError, match='public_api configuration is required'):
        await run_combined(Path('ignored.toml'))


@pytest.mark.asyncio
async def test_run_combined_raises_when_bot_polling_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    startup = AsyncMock()
    register = AsyncMock()
    cancelled = asyncio.Event()

    class RuntimeApiServer:
        def __init__(self) -> None:
            self._started = asyncio.Event()

        async def run(self) -> None:
            self._started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def wait_started(self) -> None:
            await self._started.wait()

    runtime = SimpleNamespace(
        api_server=RuntimeApiServer(),
        bot_runtime=SimpleNamespace(
            bot=build_bot(),
            backend_client=SimpleNamespace(),
            dispatcher=SimpleNamespace(start_polling=AsyncMock(return_value=None)),
        ),
        aclose=AsyncMock(),
    )

    monkeypatch.setattr('nasuchan.combined.load_config', lambda path: build_config())
    monkeypatch.setattr('nasuchan.combined.configure_logging', lambda config: None)
    monkeypatch.setattr('nasuchan.combined.create_combined_runtime', lambda config: runtime)
    monkeypatch.setattr('nasuchan.combined.perform_startup_healthcheck', startup)
    monkeypatch.setattr('nasuchan.combined.register_bot_commands', register)

    with pytest.raises(RuntimeError, match='bot-polling exited unexpectedly'):
        await run_combined(Path('ignored.toml'))

    runtime.aclose.assert_awaited_once()
    assert cancelled.is_set() is True
    startup.assert_awaited_once()
    register.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_combined_raises_when_public_api_task_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    startup = AsyncMock()
    register = AsyncMock()
    polling_cancelled = asyncio.Event()

    class RuntimeApiServer:
        def __init__(self) -> None:
            self._started = asyncio.Event()

        async def run(self) -> None:
            self._started.set()
            await asyncio.sleep(0)
            raise RuntimeError('api failed')

        async def wait_started(self) -> None:
            await self._started.wait()

    async def start_polling(_: object) -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            polling_cancelled.set()
            raise

    runtime = SimpleNamespace(
        api_server=RuntimeApiServer(),
        bot_runtime=SimpleNamespace(
            bot=build_bot(),
            backend_client=SimpleNamespace(),
            dispatcher=SimpleNamespace(start_polling=start_polling),
        ),
        aclose=AsyncMock(),
    )

    monkeypatch.setattr('nasuchan.combined.load_config', lambda path: build_config())
    monkeypatch.setattr('nasuchan.combined.configure_logging', lambda config: None)
    monkeypatch.setattr('nasuchan.combined.create_combined_runtime', lambda config: runtime)
    monkeypatch.setattr('nasuchan.combined.perform_startup_healthcheck', startup)
    monkeypatch.setattr('nasuchan.combined.register_bot_commands', register)

    with pytest.raises(RuntimeError, match='api failed'):
        await run_combined(Path('ignored.toml'))

    runtime.aclose.assert_awaited_once()
    assert polling_cancelled.is_set() is True
    startup.assert_awaited_once()
    register.assert_awaited_once()
