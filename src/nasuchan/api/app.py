from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from pathlib import PurePath

import httpx
from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiohttp import web
from aiohttp.multipart import BodyPartReader
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from nasuchan.bot.delivery import send_markdown_to_chat
from nasuchan.clients import BackendApiError, FavBackendClient
from nasuchan.config import AppConfig, PublicApiSettings
from nasuchan.services import RuntimeApiService

from .delivery_store import DeliveryClaimStatus, NotificationDeliveryStore

_AUTH_REALM = 'fav-api'
_HANIME1_VIDEOS_PATH = '/api/v2/hanime1/videos'
_NOTIFICATIONS_WEBHOOK_V2_PATH = '/api/v2/notifications/webhook'
_NOTIFICATIONS_WEBHOOK_V3_PATH = '/api/v3/notifications/webhook'
_MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_NOTIFICATION_PAYLOAD_BYTES = 64 * 1024
_MAX_WEBHOOK_REQUEST_BYTES = _MAX_TELEGRAM_PHOTO_BYTES + 512 * 1024
_MULTIPART_CHUNK_BYTES = 64 * 1024
_MAX_IDEMPOTENCY_KEY_CHARS = 200
_LOGGER = logging.getLogger(__name__)


class NotificationWebhookRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    markdown: str
    image_url: StrictStr = ''
    disable_web_page_preview: StrictBool = True
    disable_notification: StrictBool = False
    pin: StrictBool = False
    notification_id: StrictInt | None = None
    dedupe_key: StrictStr = ''
    action: StrictStr = 'send'
    occurrence_count: StrictInt = 1
    event_version: StrictInt = 1

    @field_validator('markdown')
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = 'markdown must not be empty'
            raise ValueError(msg)
        return normalized

    @field_validator('image_url')
    @classmethod
    def validate_image_url(cls, value: str) -> str:
        return value.strip()

    @field_validator('notification_id')
    @classmethod
    def validate_notification_id(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            msg = 'notification_id must be greater than 0'
            raise ValueError(msg)
        return value

    @field_validator('dedupe_key')
    @classmethod
    def validate_dedupe_key(cls, value: str) -> str:
        return value.strip()

    @field_validator('action')
    @classmethod
    def validate_action(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {'resolve', 'send', 'upsert'}:
            msg = 'action must be send, upsert, or resolve'
            raise ValueError(msg)
        return normalized

    @field_validator('occurrence_count', 'event_version')
    @classmethod
    def validate_positive_version(cls, value: int) -> int:
        if value <= 0:
            msg = 'occurrence_count and event_version must be greater than 0'
            raise ValueError(msg)
        return value


class NotificationWebhookV3Request(NotificationWebhookRequest):
    notification_id: StrictInt


@dataclass(frozen=True, slots=True)
class ParsedNotificationWebhook:
    payload: NotificationWebhookV3Request
    image: BufferedInputFile | None = None


@dataclass(frozen=True, slots=True)
class PreparedNotificationWebhook:
    idempotency_key: str
    parsed: ParsedNotificationWebhook


class MultipartPartTooLargeError(ValueError):
    pass


@dataclass(slots=True)
class PublicApiRuntime:
    bot: Bot
    admin_chat_id: int
    delivery_store: NotificationDeliveryStore
    backend_client: FavBackendClient | None = None
    service: RuntimeApiService | None = None
    http_client: httpx.AsyncClient | None = None
    manage_resources: bool = True
    manage_delivery_store: bool = True

    async def aclose(self) -> None:
        if self.manage_delivery_store:
            await self.delivery_store.aclose()
        if not self.manage_resources:
            return
        await self.bot.session.close()
        if self.http_client is not None:
            await self.http_client.aclose()
            return
        if self.backend_client is not None:
            await self.backend_client.aclose()


_RUNTIME_KEY = web.AppKey('runtime', PublicApiRuntime)
_PUBLIC_API_CONFIG_KEY = web.AppKey('public_api_config', PublicApiSettings)


def create_app(
    config: AppConfig,
    *,
    bot: Bot | None = None,
    backend_client: FavBackendClient | None = None,
    http_client: httpx.AsyncClient | None = None,
    delivery_store: NotificationDeliveryStore | None = None,
    manage_resources: bool = True,
) -> web.Application:
    public_api = _require_public_api_config(config)

    runtime_bot = bot or Bot(token=config.telegram.bot_token)
    runtime_http_client = http_client
    runtime_backend_client = backend_client
    if runtime_backend_client is None:
        fav_backend = config.backend.fav
        if fav_backend is not None:
            runtime_http_client = runtime_http_client or httpx.AsyncClient(
                base_url=fav_backend.base_url,
                timeout=fav_backend.request_timeout_seconds,
                follow_redirects=False,
            )
            runtime_backend_client = FavBackendClient(fav_backend, client=runtime_http_client)
    runtime_service = RuntimeApiService(runtime_backend_client) if runtime_backend_client is not None else None
    runtime_delivery_store = delivery_store or NotificationDeliveryStore(public_api.delivery_state_path)

    app = web.Application(client_max_size=_MAX_WEBHOOK_REQUEST_BYTES)
    app[_PUBLIC_API_CONFIG_KEY] = public_api
    app[_RUNTIME_KEY] = PublicApiRuntime(
        bot=runtime_bot,
        admin_chat_id=config.telegram.admin_chat_id,
        delivery_store=runtime_delivery_store,
        backend_client=runtime_backend_client,
        service=runtime_service,
        http_client=runtime_http_client,
        manage_resources=manage_resources,
        manage_delivery_store=delivery_store is None,
    )
    if runtime_service is not None:
        app.router.add_get(_HANIME1_VIDEOS_PATH, handle_hanime1_videos)
    app.router.add_post(_NOTIFICATIONS_WEBHOOK_V2_PATH, handle_notifications_webhook)
    app.router.add_post(_NOTIFICATIONS_WEBHOOK_V3_PATH, handle_notifications_webhook_v3)
    app.on_cleanup.append(_close_runtime)
    return app


async def handle_hanime1_videos(request: web.Request) -> web.StreamResponse:
    auth_error = _authenticate_request(request)
    if auth_error is not None:
        return auth_error

    runtime = request.app[_RUNTIME_KEY]
    if runtime.service is None:
        return _json_error(status=404, error='not_found')
    try:
        response = await runtime.service.list_hanime1_videos()
    except BackendApiError:
        _LOGGER.exception('Failed to proxy Hanime1 videos')
        return _json_error(status=500, error='internal_server_error')
    except Exception:
        _LOGGER.exception('Unexpected failure while proxying Hanime1 videos')
        return _json_error(status=500, error='internal_server_error')

    return web.json_response(response.model_dump(mode='json'))


async def handle_notifications_webhook(request: web.Request) -> web.StreamResponse:
    auth_error = _authenticate_request(request)
    if auth_error is not None:
        return auth_error

    payload = await _parse_v2_webhook_payload(request)
    if payload is None:
        return _json_error(status=400, error='invalid_payload')

    runtime = request.app[_RUNTIME_KEY]
    try:
        await send_markdown_to_chat(
            runtime.bot,
            runtime.admin_chat_id,
            payload.markdown,
            image_url=payload.image_url,
            disable_web_page_preview=payload.disable_web_page_preview,
            disable_notification=payload.disable_notification,
            pin=payload.pin,
        )
    except Exception:
        _LOGGER.exception('Failed to deliver notification webhook to Telegram')
        return _json_error(status=502, error='telegram_delivery_failed')

    return web.json_response({'status': 'delivered'})


async def handle_notifications_webhook_v3(request: web.Request) -> web.StreamResponse:
    prepared = await _prepare_v3_webhook(request)
    if isinstance(prepared, web.StreamResponse):
        return prepared

    idempotency_key = prepared.idempotency_key
    parsed = prepared.parsed
    runtime = request.app[_RUNTIME_KEY]
    claim = await runtime.delivery_store.claim(idempotency_key)
    if claim.status == DeliveryClaimStatus.DELIVERED:
        return web.json_response(
            {
                'status': 'deduplicated',
                'message_id': claim.message_id,
            },
        )
    if claim.status == DeliveryClaimStatus.PROCESSING:
        return _json_error(
            status=409,
            error='delivery_in_progress',
            headers={'Retry-After': '5'},
        )

    payload = parsed.payload
    try:
        result = await send_markdown_to_chat(
            runtime.bot,
            runtime.admin_chat_id,
            payload.markdown,
            image_url=payload.image_url,
            image=parsed.image,
            disable_web_page_preview=payload.disable_web_page_preview,
            disable_notification=payload.disable_notification,
            pin=payload.pin,
        )
    except Exception:
        with contextlib.suppress(Exception):
            await runtime.delivery_store.release(idempotency_key)
        _LOGGER.exception('Failed to deliver v3 notification webhook to Telegram')
        return _json_error(status=502, error='telegram_delivery_failed')

    await runtime.delivery_store.mark_delivered(
        idempotency_key,
        message_id=result.message_id,
    )
    return web.json_response(
        {
            'status': 'delivered',
            'message_id': result.message_id,
            'media_status': result.media_status,
        },
    )


async def _prepare_v3_webhook(request: web.Request) -> PreparedNotificationWebhook | web.StreamResponse:
    auth_error = _authenticate_request(request)
    if auth_error is not None:
        return auth_error

    idempotency_key = _idempotency_key(request)
    if idempotency_key is None:
        return _json_error(status=400, error='invalid_idempotency_key')

    try:
        parsed = await _parse_v3_webhook_payload(request)
    except MultipartPartTooLargeError:
        return _json_error(status=413, error='attachment_too_large')
    except (AssertionError, LookupError, TypeError, UnicodeDecodeError, ValueError):
        return _json_error(status=400, error='invalid_payload')
    if parsed is None:
        return _json_error(status=415, error='unsupported_media_type')
    return PreparedNotificationWebhook(idempotency_key=idempotency_key, parsed=parsed)


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


def _idempotency_key(request: web.Request) -> str | None:
    raw_value = request.headers.get('Idempotency-Key')
    if raw_value is None:
        return None
    normalized = raw_value.strip()
    if not normalized or len(normalized) > _MAX_IDEMPOTENCY_KEY_CHARS:
        return None
    return normalized


def _require_public_api_config(config: AppConfig) -> PublicApiSettings:
    if config.public_api is None:
        msg = 'public_api configuration is required to run the public HTTP API'
        raise ValueError(msg)
    return config.public_api


def _validate_v2_webhook_payload(raw_payload: object) -> NotificationWebhookRequest | None:
    if not isinstance(raw_payload, dict):
        return None
    try:
        return NotificationWebhookRequest.model_validate(raw_payload)
    except ValidationError:
        return None


def _validate_v3_webhook_payload(raw_payload: object) -> NotificationWebhookV3Request | None:
    if not isinstance(raw_payload, dict):
        return None
    try:
        return NotificationWebhookV3Request.model_validate(raw_payload)
    except ValidationError:
        return None


async def _read_multipart_part(part: BodyPartReader, *, limit: int) -> bytes:
    contents = bytearray()
    while True:
        chunk = await part.read_chunk(size=_MULTIPART_CHUNK_BYTES)
        if not chunk:
            break
        if len(contents) + len(chunk) > limit:
            raise MultipartPartTooLargeError
        contents.extend(chunk)
    return bytes(contents)


async def _parse_multipart_v3_webhook(request: web.Request) -> ParsedNotificationWebhook:
    reader = await request.multipart()
    raw_payload: object | None = None
    image: BufferedInputFile | None = None
    async for part in reader:
        if not isinstance(part, BodyPartReader):
            msg = 'Nested multipart fields are not supported'
            raise TypeError(msg)
        if part.name == 'payload':
            if raw_payload is not None:
                msg = 'Duplicate payload field'
                raise ValueError(msg)
            payload_bytes = await _read_multipart_part(part, limit=_MAX_NOTIFICATION_PAYLOAD_BYTES)
            raw_payload = json.loads(payload_bytes.decode(part.get_charset(default='utf-8')))
            continue
        if part.name != 'image' or not part.filename or image is not None:
            msg = 'Invalid multipart field'
            raise ValueError(msg)

        image_bytes = await _read_multipart_part(part, limit=_MAX_TELEGRAM_PHOTO_BYTES)
        if not image_bytes:
            msg = 'Image field cannot be empty'
            raise ValueError(msg)
        filename = PurePath(part.filename).name
        if not filename:
            msg = 'Image filename cannot be empty'
            raise ValueError(msg)
        image = BufferedInputFile(image_bytes, filename=filename)

    payload = _validate_v3_webhook_payload(raw_payload)
    if payload is None:
        msg = 'Invalid payload field'
        raise ValueError(msg)
    return ParsedNotificationWebhook(payload=payload, image=image)


async def _parse_v2_webhook_payload(request: web.Request) -> NotificationWebhookRequest | None:
    try:
        raw_payload = await request.json()
    except ValueError:
        return None
    return _validate_v2_webhook_payload(raw_payload)


async def _parse_v3_webhook_payload(request: web.Request) -> ParsedNotificationWebhook | None:
    if request.content_type == 'multipart/form-data':
        return await _parse_multipart_v3_webhook(request)
    if request.content_type != 'application/json':
        return None

    raw_payload = await request.json()
    payload = _validate_v3_webhook_payload(raw_payload)
    if payload is None:
        msg = 'Invalid JSON payload'
        raise ValueError(msg)
    return ParsedNotificationWebhook(payload=payload)


def _json_error(*, status: int, error: str, headers: dict[str, str] | None = None) -> web.Response:
    return web.json_response({'error': error}, status=status, headers=headers)


async def _close_runtime(app: web.Application) -> None:
    await app[_RUNTIME_KEY].aclose()
