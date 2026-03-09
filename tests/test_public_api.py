from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nasuchan.api import create_app
from nasuchan.clients import BackendApiTransportError, Hanime1Video, Hanime1VideoListResponse
from nasuchan.config.settings import AppConfig


def build_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
            'backend': {'fav': {'base_url': 'https://fav.example.com', 'token': 'shared-token', 'request_timeout_seconds': 15}},
            'public_api': {'bind': '127.0.0.1', 'port': 8092, 'token': 'public-runtime-api-token'},
            'polling': {
                'control_poll_interval_seconds': 2,
                'control_poll_timeout_seconds': 600,
            },
            'logging': {'level': 'INFO'},
        }
    )


class FakeBackendClient:
    def __init__(self, *, response: Hanime1VideoListResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.closed = False

    async def list_hanime1_videos(self) -> Hanime1VideoListResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_create_app_requires_public_api_config() -> None:
    config = AppConfig.model_validate(
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

    with pytest.raises(ValueError, match='public_api configuration is required'):
        create_app(config)


@pytest.mark.asyncio
async def test_hanime1_videos_requires_authorization_header() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    client = await _start_client(backend_client)

    response = await client.get('/api/v2/hanime1/videos')

    assert response.status == 401
    assert response.headers['WWW-Authenticate'] == 'Bearer realm="fav-api"'
    assert await response.json() == {'error': 'missing_authorization'}
    await client.close()
    assert backend_client.closed is True


@pytest.mark.asyncio
async def test_hanime1_videos_rejects_invalid_token() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v2/hanime1/videos',
        headers={'Authorization': 'Bearer wrong-token'},
    )

    assert response.status == 403
    assert await response.json() == {'error': 'invalid_token'}
    await client.close()


@pytest.mark.asyncio
async def test_hanime1_videos_returns_backend_payload() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[
                Hanime1Video(video_id='1001', title='First', downloaded=True, watch_url='https://example.com/watch/1001'),
                Hanime1Video(video_id='1002', title='Second', downloaded=False, watch_url='https://example.com/watch/1002'),
            ],
            total=2,
        )
    )
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v2/hanime1/videos',
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {
        'items': [
            {
                'video_id': '1001',
                'title': 'First',
                'downloaded': True,
                'uploader': None,
                'release_date': None,
                'plot': None,
                'watch_url': 'https://example.com/watch/1001',
            },
            {
                'video_id': '1002',
                'title': 'Second',
                'downloaded': False,
                'uploader': None,
                'release_date': None,
                'plot': None,
                'watch_url': 'https://example.com/watch/1002',
            },
        ],
        'total': 2,
    }
    await client.close()


@pytest.mark.asyncio
async def test_hanime1_videos_hides_backend_error_details() -> None:
    backend_client = FakeBackendClient(error=BackendApiTransportError('boom'))
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v2/hanime1/videos',
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 500
    assert await response.json() == {'error': 'internal_server_error'}
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_requires_authorization_header() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)

    response = await client.post('/api/v2/notifications/webhook', json={'markdown': '*Done*'})

    assert response.status == 401
    assert response.headers['WWW-Authenticate'] == 'Bearer realm="fav-api"'
    assert await response.json() == {'error': 'missing_authorization'}
    bot.send_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_rejects_invalid_token() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)

    response = await client.post(
        '/api/v2/notifications/webhook',
        json={'markdown': '*Done*'},
        headers={'Authorization': 'Bearer wrong-token'},
    )

    assert response.status == 403
    assert await response.json() == {'error': 'invalid_token'}
    bot.send_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_sends_markdown_v2_to_admin_chat() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)

    response = await client.post(
        '/api/v2/notifications/webhook',
        json={
            'markdown': '*Done*',
            'disable_web_page_preview': False,
            'disable_notification': True,
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args == (123456789, '*Done*')
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is False
    assert bot.send_message.await_args.kwargs['disable_notification'] is True
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload', 'content_type'),
    [
        ('{', 'application/json'),
        ({}, None),
        ({'markdown': '   '}, None),
    ],
)
async def test_notifications_webhook_rejects_invalid_payloads(payload: object, content_type: str | None) -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)

    request_kwargs = {'headers': {'Authorization': 'Bearer public-runtime-api-token'}}
    if content_type is not None:
        request_kwargs['data'] = payload
        request_kwargs['headers']['Content-Type'] = content_type
    else:
        request_kwargs['json'] = payload

    response = await client.post('/api/v2/notifications/webhook', **request_kwargs)

    assert response.status == 400
    assert await response.json() == {'error': 'invalid_payload'}
    bot.send_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_hides_telegram_delivery_error_details() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot(error=RuntimeError('boom'))
    client = await _start_client(backend_client, bot=bot)

    response = await client.post(
        '/api/v2/notifications/webhook',
        json={'markdown': '*Done*'},
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 502
    assert await response.json() == {'error': 'telegram_delivery_failed'}
    bot.send_message.assert_awaited_once()
    await client.close()


def build_bot(*, error: Exception | None = None) -> SimpleNamespace:
    send_message = AsyncMock(side_effect=error)
    return SimpleNamespace(send_message=send_message, session=SimpleNamespace(close=AsyncMock()))


async def _start_client(backend_client: FakeBackendClient, *, bot: SimpleNamespace | None = None) -> TestClient:
    app = create_app(build_config(), backend_client=backend_client, bot=bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client
