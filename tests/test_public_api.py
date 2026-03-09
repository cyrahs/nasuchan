from __future__ import annotations

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
                'notification_poll_interval_seconds': 5,
                'notification_batch_limit': 50,
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
                'notification_poll_interval_seconds': 5,
                'notification_batch_limit': 50,
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


async def _start_client(backend_client: FakeBackendClient) -> TestClient:
    app = create_app(build_config(), backend_client=backend_client)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client
