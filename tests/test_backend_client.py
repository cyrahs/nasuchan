from __future__ import annotations

import httpx
import pytest

from nasuchan.clients import (
    BackendApiConflictError,
    BackendApiForbiddenError,
    BackendApiInternalServerError,
    BackendApiNotFoundError,
    BackendApiTransportError,
    BackendApiUnauthorizedError,
    BackendApiUnprocessableError,
    BackendApiUnexpectedResponseError,
    FavBackendClient,
)
from nasuchan.config.settings import FavBackendSettings


def build_settings() -> FavBackendSettings:
    return FavBackendSettings(
        base_url='https://fav.example.com',
        token='shared-token',
        request_timeout_seconds=15,
    )


@pytest.mark.asyncio
async def test_health_omits_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/health'
        assert 'Authorization' not in request.headers
        return httpx.Response(200, json={'status': 'ok', 'generated_at': '2026-03-08T12:00:00Z'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        status = await backend_client.health()

    assert status.status == 'ok'


@pytest.mark.asyncio
async def test_authenticated_requests_include_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/control/jobs'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        return httpx.Response(
            200,
            json={'jobs': [{'key': 'bilibili', 'name': 'Bilibili', 'enabled': True, 'run_on_start': False}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        jobs = await backend_client.list_jobs()

    assert [job.key for job in jobs] == ['bilibili']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'exception_type'),
    [
        (401, BackendApiUnauthorizedError),
        (403, BackendApiForbiddenError),
        (404, BackendApiNotFoundError),
        (409, BackendApiConflictError),
        (422, BackendApiUnprocessableError),
        (500, BackendApiInternalServerError),
    ],
)
async def test_status_codes_map_to_typed_exceptions(status_code: int, exception_type: type[Exception]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={'error': 'backend_error'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(exception_type):
            await backend_client.list_jobs()


@pytest.mark.asyncio
async def test_invalid_json_raises_unexpected_response_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'not-json', headers={'Content-Type': 'application/json'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(BackendApiUnexpectedResponseError):
            await backend_client.list_jobs()


@pytest.mark.asyncio
async def test_list_notifications_uses_limit_and_status_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/notifications'
        assert request.url.params['status'] == 'unread'
        assert request.url.params['limit'] == '25'
        return httpx.Response(
            200,
            json={
                'notifications': [
                    {
                        'id': 1,
                        'kind': 'download_completed',
                        'source': 'bilibili',
                        'title': 'Done',
                        'body': 'body',
                        'link_url': '',
                        'image_url': '',
                        'payload': {},
                        'status': 'unread',
                        'created_at': '2026-03-08T12:00:00Z',
                        'read_at': None,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        notifications = await backend_client.list_notifications(limit=25)

    assert [notification.id for notification in notifications] == [1]


@pytest.mark.asyncio
async def test_get_hanime1_downloaded_ids_forwards_if_none_match_and_returns_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/runtime/hanime1/downloaded-ids'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        assert request.headers['If-None-Match'] == '"abc123"'
        return httpx.Response(
            200,
            headers={'ETag': '"def456"', 'Cache-Control': 'private, max-age=60'},
            json={
                'ids': ['1001', '1002'],
                'count': 2,
                'generated_at': '2026-03-08T12:00:00Z',
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        response = await backend_client.get_hanime1_downloaded_ids(if_none_match='"abc123"')

    assert response.not_modified is False
    assert response.etag == '"def456"'
    assert response.cache_control == 'private, max-age=60'
    assert response.payload is not None
    assert response.payload.ids == ['1001', '1002']


@pytest.mark.asyncio
async def test_get_hanime1_downloaded_ids_returns_not_modified_without_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/runtime/hanime1/downloaded-ids'
        return httpx.Response(
            304,
            headers={'ETag': '"def456"', 'Cache-Control': 'private, max-age=60'},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        response = await backend_client.get_hanime1_downloaded_ids()

    assert response.not_modified is True
    assert response.etag == '"def456"'
    assert response.payload is None


@pytest.mark.asyncio
async def test_transport_errors_raise_backend_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('boom', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(BackendApiTransportError) as exc_info:
            await backend_client.list_jobs()

    assert exc_info.value.__class__.__name__ == 'BackendApiTransportError'
