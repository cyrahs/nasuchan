from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nasuchan.api import create_app
from nasuchan.clients import BackendApiTransportError, Hanime1DownloadedIdsPayload, Hanime1DownloadedIdsResponse
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
    def __init__(self, *, response: Hanime1DownloadedIdsResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.closed = False
        self.if_none_match: str | None = None

    async def get_hanime1_downloaded_ids(self, *, if_none_match: str | None = None) -> Hanime1DownloadedIdsResponse:
        self.if_none_match = if_none_match
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
async def test_downloaded_ids_requires_authorization_header() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1DownloadedIdsResponse(
            payload=Hanime1DownloadedIdsPayload(
                ids=['1001'],
                count=1,
                generated_at='2026-03-08T12:00:00Z',
            ),
        )
    )
    client = await _start_client(backend_client)

    response = await client.get('/api/v1/runtime/hanime1/downloaded-ids')

    assert response.status == 401
    assert response.headers['WWW-Authenticate'] == 'Bearer realm="fav-api"'
    assert await response.json() == {'error': 'missing_authorization'}
    await client.close()
    assert backend_client.closed is True


@pytest.mark.asyncio
async def test_downloaded_ids_rejects_invalid_token() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1DownloadedIdsResponse(
            payload=Hanime1DownloadedIdsPayload(
                ids=['1001'],
                count=1,
                generated_at='2026-03-08T12:00:00Z',
            ),
        )
    )
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v1/runtime/hanime1/downloaded-ids',
        headers={'Authorization': 'Bearer wrong-token'},
    )

    assert response.status == 403
    assert await response.json() == {'error': 'invalid_token'}
    await client.close()


@pytest.mark.asyncio
async def test_downloaded_ids_returns_payload_and_proxy_headers() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1DownloadedIdsResponse(
            etag='"etag-123"',
            cache_control='private, max-age=60',
            payload=Hanime1DownloadedIdsPayload(
                ids=['1001', '1002'],
                count=2,
                generated_at='2026-03-08T12:00:00Z',
            ),
        )
    )
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v1/runtime/hanime1/downloaded-ids',
        headers={
            'Authorization': 'Bearer public-runtime-api-token',
            'If-None-Match': '"etag-old"',
        },
    )

    assert backend_client.if_none_match == '"etag-old"'
    assert response.status == 200
    assert response.headers['ETag'] == '"etag-123"'
    assert response.headers['Cache-Control'] == 'private, max-age=60'
    assert await response.json() == {
        'ids': ['1001', '1002'],
        'count': 2,
        'generated_at': '2026-03-08T12:00:00Z',
    }
    await client.close()


@pytest.mark.asyncio
async def test_downloaded_ids_returns_304_without_body() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1DownloadedIdsResponse(
            not_modified=True,
            etag='"etag-123"',
            cache_control='private, max-age=60',
        )
    )
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v1/runtime/hanime1/downloaded-ids',
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 304
    assert response.headers['ETag'] == '"etag-123"'
    assert await response.text() == ''
    await client.close()


@pytest.mark.asyncio
async def test_downloaded_ids_hides_backend_error_details() -> None:
    backend_client = FakeBackendClient(error=BackendApiTransportError('boom'))
    client = await _start_client(backend_client)

    response = await client.get(
        '/api/v1/runtime/hanime1/downloaded-ids',
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
