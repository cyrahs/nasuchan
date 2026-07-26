from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import BufferedInputFile
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto

from nasuchan.api import create_app
from nasuchan.api.delivery_store import DeliveryClaim, DeliveryClaimStatus
import nasuchan.bot.delivery as delivery_module
from nasuchan.clients import BackendApiTransportError, Hanime1Video, Hanime1VideoListResponse
from nasuchan.config.settings import AppConfig

_DATABASE_CONFIG = {
    'host': 'postgresql.example.com',
    'port': 5432,
    'dbname': 'nasuchan',
    'user': 'nasuchan',
    'password': 'database-password',
    'connect_timeout_seconds': 5,
}


def build_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
            'backend': {'fav': {'base_url': 'https://fav.example.com', 'token': 'shared-token', 'request_timeout_seconds': 15}},
            'database': _DATABASE_CONFIG,
            'public_api': {
                'bind': '127.0.0.1',
                'port': 8092,
                'token': 'public-runtime-api-token',
            },
            'polling': {
                'control_poll_interval_seconds': 2,
                'control_poll_timeout_seconds': 600,
            },
            'logging': {'level': 'INFO'},
        }
    )


def build_aninamer_only_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            'telegram': {'bot_token': '123456:telegram-bot-token', 'admin_chat_id': 123456789},
            'backend': {'aninamer': {'base_url': 'https://aninamer.example.com', 'token': 'aninamer-token', 'request_timeout_seconds': 15}},
            'database': _DATABASE_CONFIG,
            'public_api': {
                'bind': '127.0.0.1',
                'port': 8092,
                'token': 'public-runtime-api-token',
            },
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


class FakeDeliveryStore:
    def __init__(self) -> None:
        self._states: dict[str, tuple[str, int | None]] = {}

    async def open(self) -> None:
        return

    async def claim(self, idempotency_key: str) -> DeliveryClaim:
        existing = self._states.get(idempotency_key)
        if existing is None:
            self._states[idempotency_key] = ('processing', None)
            return DeliveryClaim(DeliveryClaimStatus.ACQUIRED)
        status, message_id = existing
        if status == 'delivered':
            return DeliveryClaim(DeliveryClaimStatus.DELIVERED, message_id=message_id)
        return DeliveryClaim(DeliveryClaimStatus.PROCESSING)

    async def mark_delivered(self, idempotency_key: str, *, message_id: int | None) -> None:
        self._states[idempotency_key] = ('delivered', message_id)

    async def release(self, idempotency_key: str) -> None:
        self._states.pop(idempotency_key, None)

    async def aclose(self) -> None:
        return


def telegram_bad_request(message: str = 'bad image') -> TelegramBadRequest:
    return TelegramBadRequest(method=SendPhoto(chat_id=123456789, photo='https://example.com/poster.jpg'), message=message)


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
async def test_create_app_requires_database_config() -> None:
    raw_config = build_config().model_dump()
    raw_config['database'] = None
    config = AppConfig.model_validate(raw_config)

    with pytest.raises(ValueError, match='database configuration is required'):
        create_app(config)


@pytest.mark.asyncio
async def test_create_app_without_fav_backend_still_allows_notification_webhook() -> None:
    bot = build_bot()
    app = create_app(build_aninamer_only_config(), bot=bot, delivery_store=FakeDeliveryStore())
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    hanime_response = await client.get(
        '/api/v2/hanime1/videos',
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )
    webhook_response = await client.post(
        '/api/v2/notifications/webhook',
        json={'markdown': '*Done*'},
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert hanime_response.status == 404
    assert webhook_response.status == 200
    assert await webhook_response.json() == {'status': 'delivered'}
    await client.close()


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
    bot.send_photo.assert_not_awaited()
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
    bot.send_photo.assert_not_awaited()
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
            'notification_id': 41,
            'dedupe_key': 'job_failed:bilibili:download',
            'action': 'upsert',
            'occurrence_count': 2,
            'event_version': 3,
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_message.assert_awaited_once()
    bot.send_photo.assert_not_awaited()
    assert bot.send_message.await_args.args == (123456789, '*Done*')
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is False
    assert bot.send_message.await_args.kwargs['disable_notification'] is True
    bot.pin_chat_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_pins_message_when_requested() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot(message_id=456)
    client = await _start_client(backend_client, bot=bot)

    response = await client.post(
        '/api/v2/notifications/webhook',
        json={
            'markdown': '*Done*',
            'disable_notification': True,
            'pin': True,
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_message.assert_awaited_once()
    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=456,
        disable_notification=True,
    )
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_sends_photo_when_image_url_is_present() -> None:
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
            'image_url': 'https://example.com/poster.jpg',
            'disable_notification': True,
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert bot.send_photo.await_args.kwargs['caption'] == '*Done*'
    assert bot.send_photo.await_args.kwargs['parse_mode'] == 'MarkdownV2'
    assert bot.send_photo.await_args.kwargs['disable_notification'] is True
    bot.pin_chat_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_v3_webhook_uploads_photo_file_and_deduplicates() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)
    form = FormData()
    form.add_field(
        'payload',
        json.dumps(
            {
                'notification_id': 42,
                'markdown': '*Done*',
                'image_url': 'https://example.com/fallback.jpg',
                'disable_notification': True,
                'pin': True,
                'dedupe_key': 'job_failed:bilibili:download',
                'action': 'upsert',
                'occurrence_count': 2,
                'event_version': 3,
            }
        ),
    )
    form.add_field('image', b'image-data', filename='poster.png', content_type='image/png')

    response = await client.post(
        '/api/v3/notifications/webhook',
        data=form,
        headers={
            'Authorization': 'Bearer public-runtime-api-token',
            'Idempotency-Key': 'fav:42',
        },
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered', 'message_id': 789, 'media_status': 'uploaded'}
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    image = bot.send_photo.await_args.args[1]
    assert isinstance(image, BufferedInputFile)
    assert image.data == b'image-data'
    assert image.filename == 'poster.png'
    assert bot.send_photo.await_args.kwargs['caption'] == '*Done*'
    assert bot.send_photo.await_args.kwargs['disable_notification'] is True
    bot.pin_chat_message.assert_awaited_once_with(
        chat_id=123456789,
        message_id=789,
        disable_notification=True,
    )

    duplicate_form = FormData()
    duplicate_form.add_field(
        'payload',
        json.dumps(
            {
                'notification_id': 42,
                'markdown': '*Done*',
                'image_url': 'https://example.com/fallback.jpg',
                'disable_notification': True,
                'pin': True,
            }
        ),
    )
    duplicate_form.add_field('image', b'image-data', filename='poster.png', content_type='image/png')
    duplicate_response = await client.post(
        '/api/v3/notifications/webhook',
        data=duplicate_form,
        headers={
            'Authorization': 'Bearer public-runtime-api-token',
            'Idempotency-Key': 'fav:42',
        },
    )

    assert duplicate_response.status == 200
    assert await duplicate_response.json() == {'status': 'deduplicated', 'message_id': 789}
    bot.send_photo.assert_awaited_once()
    assert bot.pin_chat_message.await_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_notifications_v3_webhook_rejects_oversized_photo_without_buffering_delivery() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)
    form = FormData()
    form.add_field('payload', json.dumps({'notification_id': 43, 'markdown': '*Done*'}))
    form.add_field('image', b'x' * (10 * 1024 * 1024 + 1), filename='poster.png', content_type='image/png')

    response = await client.post(
        '/api/v3/notifications/webhook',
        data=form,
        headers={
            'Authorization': 'Bearer public-runtime-api-token',
            'Idempotency-Key': 'fav:43',
        },
    )

    assert response.status == 413
    assert await response.json() == {'error': 'attachment_too_large'}
    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_v3_webhook_returns_400_for_truncated_multipart() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    client = await _start_client(backend_client, bot=bot)
    boundary = 'broken-boundary'
    body = (
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="payload"\r\n'
        'Content-Type: application/json\r\n\r\n'
        '{"notification_id":44,"markdown":"*Done*"}\r\n'
    )

    response = await client.post(
        '/api/v3/notifications/webhook',
        data=body,
        headers={
            'Authorization': 'Bearer public-runtime-api-token',
            'Idempotency-Key': 'fav:44',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
    )

    assert response.status == 400
    assert await response.json() == {'error': 'invalid_payload'}
    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_falls_back_to_photo_then_message_for_long_caption() -> None:
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
            'markdown': 'x' * 1025,
            'image_url': 'https://example.com/poster.jpg',
            'disable_web_page_preview': False,
            'disable_notification': True,
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    assert bot.send_photo.await_args.args == (123456789, 'https://example.com/poster.jpg')
    assert 'caption' not in bot.send_photo.await_args.kwargs
    assert bot.send_photo.await_args.kwargs['disable_notification'] is True
    assert bot.send_message.await_args.args == (123456789, 'x' * 1025)
    assert bot.send_message.await_args.kwargs['disable_web_page_preview'] is False
    assert bot.send_message.await_args.kwargs['disable_notification'] is True
    bot.pin_chat_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_notifications_webhook_falls_back_to_text_when_image_delivery_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    bot.send_photo.side_effect = telegram_bad_request()

    async def fail_download(_image_url: str) -> None:
        raise delivery_module._ImageDownloadError('bad download')  # noqa: SLF001

    monkeypatch.setattr(delivery_module, '_download_image_to_temp_file', fail_download)
    client = await _start_client(backend_client, bot=bot)

    response = await client.post(
        '/api/v2/notifications/webhook',
        json={
            'markdown': '*Done*',
            'image_url': 'https://example.com/poster.jpg',
        },
        headers={'Authorization': 'Bearer public-runtime-api-token'},
    )

    assert response.status == 200
    assert await response.json() == {'status': 'delivered'}
    bot.send_photo.assert_awaited_once()
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args == (123456789, '*Done*\n\n图片发送失败, 已改为纯文本通知')
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('payload', 'content_type'),
    [
        ('{', 'application/json'),
        ({}, None),
        ({'markdown': '   '}, None),
        ({'markdown': '*Done*', 'image_url': 123}, None),
        ({'markdown': '*Done*', 'pin': 'true'}, None),
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
    bot.send_photo.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()
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
    bot.send_photo.assert_not_awaited()
    bot.pin_chat_message.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_create_app_can_skip_resource_management() -> None:
    backend_client = FakeBackendClient(
        response=Hanime1VideoListResponse(
            items=[Hanime1Video(video_id='1001', title='Title', downloaded=True, watch_url='https://example.com/watch/1001')],
            total=1,
        )
    )
    bot = build_bot()
    app = create_app(
        build_config(),
        backend_client=backend_client,
        bot=bot,
        manage_resources=False,
    )

    await app.cleanup()

    assert backend_client.closed is False
    bot.session.close.assert_not_awaited()


def build_bot(*, error: Exception | None = None, message_id: int = 456, photo_message_id: int = 789) -> SimpleNamespace:
    send_message = AsyncMock(side_effect=error)
    send_photo = AsyncMock(side_effect=error)
    if error is None:
        send_message.return_value = SimpleNamespace(message_id=message_id)
        send_photo.return_value = SimpleNamespace(message_id=photo_message_id)
    return SimpleNamespace(
        send_message=send_message,
        send_photo=send_photo,
        pin_chat_message=AsyncMock(),
        session=SimpleNamespace(close=AsyncMock()),
    )


async def _start_client(backend_client: FakeBackendClient, *, bot: SimpleNamespace | None = None) -> TestClient:
    app = create_app(
        build_config(),
        backend_client=backend_client,
        bot=bot,
        delivery_store=FakeDeliveryStore(),
    )
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client
