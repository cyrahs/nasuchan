from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from aiohttp import web

from nasuchan.clients import BackendApiError, FavBackendClient
from nasuchan.config import AppConfig, PublicApiSettings
from nasuchan.services import RuntimeApiService

_AUTH_REALM = 'fav-api'
_HANIME1_VIDEOS_PATH = '/api/v2/hanime1/videos'
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PublicApiRuntime:
    backend_client: FavBackendClient
    service: RuntimeApiService
    http_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self.http_client is not None:
            await self.http_client.aclose()
            return
        await self.backend_client.aclose()


_RUNTIME_KEY = web.AppKey('runtime', PublicApiRuntime)
_PUBLIC_API_CONFIG_KEY = web.AppKey('public_api_config', PublicApiSettings)


def create_app(
    config: AppConfig,
    *,
    backend_client: FavBackendClient | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> web.Application:
    public_api = _require_public_api_config(config)

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

    app = web.Application()
    app[_PUBLIC_API_CONFIG_KEY] = public_api
    app[_RUNTIME_KEY] = PublicApiRuntime(
        backend_client=runtime_backend_client,
        service=RuntimeApiService(runtime_backend_client),
        http_client=runtime_http_client,
    )
    app.router.add_get(_HANIME1_VIDEOS_PATH, handle_hanime1_videos)
    app.on_cleanup.append(_close_runtime)
    return app


async def handle_hanime1_videos(request: web.Request) -> web.StreamResponse:
    auth_error = _authenticate_request(request)
    if auth_error is not None:
        return auth_error

    runtime = request.app[_RUNTIME_KEY]
    try:
        response = await runtime.service.list_hanime1_videos()
    except BackendApiError:
        _LOGGER.exception('Failed to proxy Hanime1 videos')
        return _json_error(status=500, error='internal_server_error')
    except Exception:
        _LOGGER.exception('Unexpected failure while proxying Hanime1 videos')
        return _json_error(status=500, error='internal_server_error')

    return web.json_response(response.model_dump(mode='json'))


def _authenticate_request(request: web.Request) -> web.Response | None:
    authorization = request.headers.get('Authorization')
    token = _extract_bearer_token(authorization)
    if token is None:
        return _json_error(
            status=401,
            error='missing_authorization',
            headers={'WWW-Authenticate': f'Bearer realm="{_AUTH_REALM}"'},
        )

    public_api = request.app[_PUBLIC_API_CONFIG_KEY]
    if token != public_api.token:
        return _json_error(status=403, error='invalid_token')
    return None


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(' ')
    normalized_token = token.strip()
    if scheme.casefold() != 'bearer' or not normalized_token:
        return None
    return normalized_token


def _require_public_api_config(config: AppConfig) -> PublicApiSettings:
    if config.public_api is None:
        msg = 'public_api configuration is required to run the public HTTP API'
        raise ValueError(msg)
    return config.public_api


def _json_error(*, status: int, error: str, headers: dict[str, str] | None = None) -> web.Response:
    return web.json_response({'error': error}, status=status, headers=headers)


async def _close_runtime(app: web.Application) -> None:
    await app[_RUNTIME_KEY].aclose()
