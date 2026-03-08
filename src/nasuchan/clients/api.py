from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from nasuchan.config import BackendApiSettings

from .exceptions import (
    BackendApiBadRequestError,
    BackendApiConflictError,
    BackendApiError,
    BackendApiForbiddenError,
    BackendApiInternalServerError,
    BackendApiNotFoundError,
    BackendApiTransportError,
    BackendApiUnauthorizedError,
    BackendApiUnexpectedResponseError,
    BackendApiUnprocessableError,
)
from .models import ControlRequest, Hanime1Seed, HealthStatus, JobSummary, NotificationRecord

_HEALTH_PATH = '/api/v1/health'
_CONTROL_JOBS_PATH = '/api/v1/control/jobs'
_CONTROL_REQUESTS_PATH = '/api/v1/control/requests'
_HANIME1_SEEDS_PATH = '/api/v1/control/runtime/hanime1/seeds'
_NOTIFICATIONS_PATH = '/api/v1/notifications'
_NOTIFICATIONS_ACK_PATH = '/api/v1/notifications/ack'


class FavBackendClient:
    def __init__(self, config: BackendApiSettings, client: httpx.AsyncClient | None = None) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> HealthStatus:
        payload = await self._request_json('GET', _HEALTH_PATH, authenticated=False)
        return HealthStatus.model_validate(payload)

    async def list_jobs(self) -> list[JobSummary]:
        payload = await self._request_json('GET', _CONTROL_JOBS_PATH)
        return [JobSummary.model_validate(item) for item in payload.get('jobs', [])]

    async def create_trigger_request(self, target: str) -> ControlRequest:
        payload = await self._request_json(
            'POST',
            _CONTROL_REQUESTS_PATH,
            json_body={'kind': 'trigger_job', 'target': target},
        )
        return ControlRequest.model_validate(payload)

    async def get_control_request(self, request_id: int) -> ControlRequest:
        payload = await self._request_json('GET', f'{_CONTROL_REQUESTS_PATH}/{request_id}')
        return ControlRequest.model_validate(payload)

    async def list_hanime1_seeds(self) -> list[Hanime1Seed]:
        payload = await self._request_json('GET', _HANIME1_SEEDS_PATH)
        return [Hanime1Seed.model_validate(item) for item in payload.get('seeds', [])]

    async def add_hanime1_seed(self, raw_seed: str) -> Hanime1Seed:
        payload = await self._request_json('POST', _HANIME1_SEEDS_PATH, json_body={'seed': raw_seed})
        return Hanime1Seed.model_validate(payload)

    async def delete_hanime1_seed(self, video_id: str) -> Hanime1Seed:
        payload = await self._request_json('DELETE', f'{_HANIME1_SEEDS_PATH}/{video_id}')
        return Hanime1Seed.model_validate(payload)

    async def list_notifications(self, *, status: str = 'unread', limit: int = 50) -> list[NotificationRecord]:
        payload = await self._request_json(
            'GET',
            _NOTIFICATIONS_PATH,
            params={'status': status, 'limit': str(limit)},
        )
        return [NotificationRecord.model_validate(item) for item in payload.get('notifications', [])]

    async def ack_notifications(self, ids: list[int]) -> int:
        payload = await self._request_json('POST', _NOTIFICATIONS_ACK_PATH, json_body={'ids': ids})
        updated = payload.get('updated', 0)
        if not isinstance(updated, int):
            msg = 'Backend returned a non-integer notifications ack count'
            raise BackendApiUnexpectedResponseError(msg, path=_NOTIFICATIONS_ACK_PATH, response_body=json.dumps(payload))
        return updated

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        params: Mapping[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {'Accept': 'application/json'}
        if authenticated:
            headers['Authorization'] = f'Bearer {self._config.token}'
        try:
            response = await self._client.request(
                method=method,
                url=path,
                headers=headers,
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            msg = f'Failed to reach backend endpoint {path}'
            raise BackendApiTransportError(msg, path=path) from exc
        if response.status_code >= 400:
            self._raise_for_status(path, response)
        try:
            payload = response.json()
        except ValueError as exc:
            msg = f'Backend endpoint {path} returned invalid JSON'
            raise BackendApiUnexpectedResponseError(msg, status_code=response.status_code, path=path, response_body=response.text) from exc
        if not isinstance(payload, dict):
            msg = f'Backend endpoint {path} returned a non-object JSON payload'
            raise BackendApiUnexpectedResponseError(msg, status_code=response.status_code, path=path, response_body=response.text)
        return payload

    def _raise_for_status(self, path: str, response: httpx.Response) -> None:
        error_code = self._extract_error_code(response)
        message = f'Backend request failed with status {response.status_code} for {path}'
        kwargs = {
            'message': message,
            'status_code': response.status_code,
            'error_code': error_code,
            'path': path,
            'response_body': response.text,
        }
        status_map: dict[int, type[BackendApiError]] = {
            400: BackendApiBadRequestError,
            401: BackendApiUnauthorizedError,
            403: BackendApiForbiddenError,
            404: BackendApiNotFoundError,
            409: BackendApiConflictError,
            422: BackendApiUnprocessableError,
            500: BackendApiInternalServerError,
        }
        exception_type = status_map.get(response.status_code, BackendApiUnexpectedResponseError)
        raise exception_type(**kwargs)

    @staticmethod
    def _extract_error_code(response: httpx.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get('error')
        return error if isinstance(error, str) else None
