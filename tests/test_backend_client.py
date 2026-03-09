from __future__ import annotations

import pytest
import httpx

from nasuchan.clients import (
    AninamerClient,
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
from nasuchan.config.settings import AninamerBackendSettings, FavBackendSettings


def build_settings() -> FavBackendSettings:
    return FavBackendSettings(
        base_url='https://fav.example.com',
        token='shared-token',
        request_timeout_seconds=15,
    )


def build_aninamer_settings() -> AninamerBackendSettings:
    return AninamerBackendSettings(
        base_url='https://aninamer.example.com',
        token='aninamer-token',
        request_timeout_seconds=15,
    )


@pytest.mark.asyncio
async def test_health_omits_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/healthz'
        assert 'Authorization' not in request.headers
        return httpx.Response(200, json={'status': 'ok', 'generated_at': '2026-03-08T12:00:00Z'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        status = await backend_client.health()

    assert status.status == 'ok'


@pytest.mark.asyncio
async def test_authenticated_requests_include_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v2/jobs'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        return httpx.Response(
            200,
            json={
                'items': [{'key': 'bilibili', 'name': 'Bilibili', 'enabled': True, 'run_on_start': False, 'cron': '0 * * * *'}],
                'total': 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        jobs = await backend_client.list_jobs()

    assert [job.key for job in jobs] == ['bilibili']
    assert jobs[0].cron == '0 * * * *'


@pytest.mark.asyncio
async def test_create_job_request_sends_target_payload_and_parses_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v2/job-requests'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        assert request.content == b'{"target":"bilibili"}'
        return httpx.Response(
            202,
            json={
                'id': 10,
                'target': 'bilibili',
                'status': 'pending',
                'requested_at': '2026-03-08T12:00:00Z',
                'result': '',
                'error': '',
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        request = await backend_client.create_job_request('bilibili')

    assert request.id == 10
    assert request.status == 'pending'


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
        return httpx.Response(status_code, json={'error': {'code': 'backend_error', 'message': 'boom', 'details': None}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(exception_type) as exc_info:
            await backend_client.list_jobs()

    assert exc_info.value.error_code == 'backend_error'


@pytest.mark.asyncio
async def test_invalid_json_raises_unexpected_response_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'not-json', headers={'Content-Type': 'application/json'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(BackendApiUnexpectedResponseError):
            await backend_client.list_jobs()


@pytest.mark.asyncio
async def test_list_hanime1_videos_returns_items_and_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v2/hanime1/videos'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        return httpx.Response(
            200,
            json={
                'items': [
                    {
                        'video_id': '1001',
                        'title': 'Example',
                        'downloaded': True,
                        'uploader': 'uploader',
                        'release_date': '2026-03-08',
                        'plot': None,
                        'watch_url': 'https://example.com/watch/1001',
                    }
                ],
                'total': 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        response = await backend_client.list_hanime1_videos()

    assert response.total == 1
    assert [video.video_id for video in response.items] == ['1001']


@pytest.mark.asyncio
async def test_delete_hanime1_seed_accepts_no_content_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v2/hanime1/seeds/12488'
        assert request.headers['Authorization'] == 'Bearer shared-token'
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        response = await backend_client.delete_hanime1_seed('12488')

    assert response is None


@pytest.mark.asyncio
async def test_transport_errors_raise_backend_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('boom', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://fav.example.com') as http_client:
        backend_client = FavBackendClient(build_settings(), client=http_client)
        with pytest.raises(BackendApiTransportError) as exc_info:
            await backend_client.list_jobs()

    assert exc_info.value.__class__.__name__ == 'BackendApiTransportError'


@pytest.mark.asyncio
async def test_aninamer_health_omits_authorization_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/healthz'
        assert 'Authorization' not in request.headers
        return httpx.Response(200, json={'status': 'ok'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://aninamer.example.com') as http_client:
        backend_client = AninamerClient(build_aninamer_settings(), client=http_client)
        status = await backend_client.health()

    assert status.status == 'ok'


@pytest.mark.asyncio
async def test_aninamer_runtime_uses_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/runtime'
        assert request.headers['Authorization'] == 'Bearer aninamer-token'
        return httpx.Response(
            200,
            json={
                'auto_apply': False,
                'settle_seconds': 30,
                'scan_interval_seconds': 60,
                'watch_root_keys': ['downloads'],
                'last_scan_at': '2026-03-08T12:00:00Z',
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://aninamer.example.com') as http_client:
        backend_client = AninamerClient(build_aninamer_settings(), client=http_client)
        runtime = await backend_client.get_runtime()

    assert runtime.watch_root_keys == ['downloads']


@pytest.mark.asyncio
async def test_aninamer_create_scan_now_request_omits_job_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/job-requests'
        assert request.headers['Authorization'] == 'Bearer aninamer-token'
        assert request.content == b'{"action":"scan_now"}'
        return httpx.Response(
            202,
            json={
                'id': 7,
                'action': 'scan_now',
                'status': 'pending',
                'job_id': None,
                'created_at': '2026-03-08T12:00:00Z',
                'updated_at': '2026-03-08T12:00:00Z',
                'started_at': None,
                'finished_at': None,
                'error_message': None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://aninamer.example.com') as http_client:
        backend_client = AninamerClient(build_aninamer_settings(), client=http_client)
        request = await backend_client.create_job_request('scan_now')

    assert request.action == 'scan_now'
    assert request.job_id is None


@pytest.mark.asyncio
async def test_aninamer_create_apply_job_request_includes_job_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/job-requests'
        assert request.headers['Authorization'] == 'Bearer aninamer-token'
        assert request.content == b'{"action":"apply_job","job_id":101}'
        return httpx.Response(
            202,
            json={
                'id': 8,
                'action': 'apply_job',
                'status': 'pending',
                'job_id': 101,
                'created_at': '2026-03-08T12:00:00Z',
                'updated_at': '2026-03-08T12:00:00Z',
                'started_at': None,
                'finished_at': None,
                'error_message': None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://aninamer.example.com') as http_client:
        backend_client = AninamerClient(build_aninamer_settings(), client=http_client)
        request = await backend_client.create_job_request('apply_job', job_id=101)

    assert request.action == 'apply_job'
    assert request.job_id == 101


@pytest.mark.asyncio
async def test_aninamer_status_parses_summary_and_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v1/status'
        assert request.headers['Authorization'] == 'Bearer aninamer-token'
        return httpx.Response(
            200,
            json={
                'summary': {
                    'pending_count': 1,
                    'planning_count': 0,
                    'planned_count': 1,
                    'apply_requested_count': 0,
                    'applying_count': 0,
                    'failed_count': 1,
                },
                'pending_items': [
                    {
                        'job_id': 101,
                        'series_name': 'ShowA',
                        'watch_root_key': 'downloads',
                        'status': 'planned',
                        'updated_at': '2026-03-08T12:00:00Z',
                        'tmdb_id': 123,
                        'video_moves_count': 1,
                        'subtitle_moves_count': 0,
                        'error_stage': None,
                        'error_message': None,
                    }
                ],
                'failed_items': [
                    {
                        'job_id': 102,
                        'series_name': 'ShowB',
                        'watch_root_key': 'downloads',
                        'status': 'failed',
                        'updated_at': '2026-03-08T12:01:00Z',
                        'tmdb_id': None,
                        'video_moves_count': 0,
                        'subtitle_moves_count': 0,
                        'error_stage': 'plan',
                        'error_message': 'LLMOutputError',
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='https://aninamer.example.com') as http_client:
        backend_client = AninamerClient(build_aninamer_settings(), client=http_client)
        response = await backend_client.get_status()

    assert response.summary.planned_count == 1
    assert response.pending_items[0].job_id == 101
    assert response.failed_items[0].error_stage == 'plan'
