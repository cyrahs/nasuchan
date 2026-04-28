from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
from pathlib import Path
import socket
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

_TELEGRAM_CAPTION_LIMIT = 1024
_TELEGRAM_MESSAGE_LIMIT = 4096
_IMAGE_DOWNLOAD_MAX_BYTES = 10 * 1024 * 1024
_IMAGE_DOWNLOAD_MAX_REDIRECTS = 3
_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 30
_IMAGE_FAILURE_NOTICE = '\n\n图片发送失败, 已改为纯文本通知'
_IMAGE_FAILURE_NOTICE_TEXT = _IMAGE_FAILURE_NOTICE.strip()
_IMAGE_DOWNLOAD_ALLOWED_PORTS = {80, 443}
_IMAGE_DOWNLOAD_ALLOWED_SCHEMES = {'http', 'https'}
_IMAGE_CONTENT_TYPE_SUFFIXES = {
    'image/gif': '.gif',
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _TemporaryImage:
    path: Path
    filename: str


class _ImageDownloadError(RuntimeError):
    pass


async def send_markdown_to_chat(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    image_url: str = '',
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
    pin: bool = False,
) -> None:
    normalized_image_url = image_url.strip()
    if not normalized_image_url:
        message = await _send_text_message(
            bot,
            chat_id,
            markdown,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
        )
        if pin:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message.message_id,
                disable_notification=disable_notification,
            )
        return

    if len(markdown) <= _TELEGRAM_CAPTION_LIMIT:
        message = await _send_photo_with_fallback(
            bot,
            chat_id,
            normalized_image_url,
            caption=markdown,
            disable_notification=disable_notification,
        )
        if message is None:
            message = await _send_text_with_image_failure_notice(
                bot,
                chat_id,
                markdown,
                disable_web_page_preview=disable_web_page_preview,
                disable_notification=disable_notification,
            )
        if pin:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message.message_id,
                disable_notification=disable_notification,
            )
        return

    photo_message = await _send_photo_with_fallback(
        bot,
        chat_id,
        normalized_image_url,
        disable_notification=disable_notification,
    )
    message = (
        await _send_text_message(
            bot,
            chat_id,
            markdown,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
        )
        if photo_message is not None
        else await _send_text_with_image_failure_notice(
            bot,
            chat_id,
            markdown,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
        )
    )
    if pin:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=message.message_id,
            disable_notification=disable_notification,
        )


async def _send_text_message(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    disable_web_page_preview: bool,
    disable_notification: bool,
) -> Any:
    return await bot.send_message(
        chat_id,
        markdown,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=disable_web_page_preview,
        disable_notification=disable_notification,
    )


async def _send_text_with_image_failure_notice(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    disable_web_page_preview: bool,
    disable_notification: bool,
) -> Any:
    markdown_with_notice = _with_image_failure_notice(markdown)
    if len(markdown_with_notice) <= _TELEGRAM_MESSAGE_LIMIT:
        return await _send_text_message(
            bot,
            chat_id,
            markdown_with_notice,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
        )

    message = await _send_text_message(
        bot,
        chat_id,
        markdown,
        disable_web_page_preview=disable_web_page_preview,
        disable_notification=disable_notification,
    )
    await _send_text_message(
        bot,
        chat_id,
        _IMAGE_FAILURE_NOTICE_TEXT,
        disable_web_page_preview=True,
        disable_notification=disable_notification,
    )
    return message


async def _send_photo_with_fallback(
    bot: Bot,
    chat_id: int,
    image_url: str,
    *,
    caption: str | None = None,
    disable_notification: bool,
) -> Any | None:
    try:
        return await _send_photo(
            bot,
            chat_id,
            image_url,
            caption=caption,
            disable_notification=disable_notification,
        )
    except TelegramBadRequest:
        _LOGGER.warning('Telegram rejected notification image URL; trying local upload', exc_info=True)

    try:
        temporary_image = await _download_image_to_temp_file(image_url)
    except _ImageDownloadError:
        _LOGGER.warning('Failed to download notification image for local Telegram upload', exc_info=True)
        return None

    try:
        return await _send_photo(
            bot,
            chat_id,
            FSInputFile(temporary_image.path, filename=temporary_image.filename),
            caption=caption,
            disable_notification=disable_notification,
        )
    except TelegramBadRequest:
        _LOGGER.warning('Telegram rejected locally uploaded notification image; falling back to text', exc_info=True)
        return None
    finally:
        temporary_image.path.unlink(missing_ok=True)


async def _send_photo(
    bot: Bot,
    chat_id: int,
    photo: str | FSInputFile,
    *,
    caption: str | None = None,
    disable_notification: bool,
) -> Any:
    kwargs: dict[str, Any] = {'disable_notification': disable_notification}
    if caption is not None:
        kwargs['caption'] = caption
        kwargs['parse_mode'] = ParseMode.MARKDOWN_V2
    return await bot.send_photo(chat_id, photo, **kwargs)


async def _download_image_to_temp_file(image_url: str) -> _TemporaryImage:
    path: Path | None = None
    completed = False
    try:
        current_url = image_url
        async with httpx.AsyncClient(timeout=_IMAGE_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=False) as client:
            for _ in range(_IMAGE_DOWNLOAD_MAX_REDIRECTS + 1):
                await _validate_download_url(current_url)
                async with client.stream('GET', current_url) as response:
                    if response.is_redirect:
                        current_url = _redirect_target(current_url, response)
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get('content-type', '').partition(';')[0].strip().lower()
                    _validate_image_response(content_type, response.headers.get('content-length'))

                    suffix = _image_suffix(current_url, content_type)
                    filename = _image_filename(current_url, suffix)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                        path = Path(handle.name)
                        total_bytes = 0
                        async for chunk in response.aiter_bytes():
                            total_bytes += len(chunk)
                            _validate_download_size(total_bytes)
                            handle.write(chunk)

                    _validate_downloaded_body(path, total_bytes)
                    completed = True
                    return _TemporaryImage(path=path, filename=filename)

        _raise_too_many_image_redirects()
    except _ImageDownloadError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise _ImageDownloadError(f'failed to download image: {exc}') from exc
    finally:
        if path is not None and not completed:
            path.unlink(missing_ok=True)


def _with_image_failure_notice(markdown: str) -> str:
    return f'{markdown}{_IMAGE_FAILURE_NOTICE}'


def _parse_content_length(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _raise_too_many_image_redirects() -> None:
    raise _ImageDownloadError('too many image redirects')


async def _validate_download_url(image_url: str) -> None:
    parsed = urlparse(image_url)
    if parsed.scheme not in _IMAGE_DOWNLOAD_ALLOWED_SCHEMES:
        raise _ImageDownloadError(f'image URL scheme is not allowed: {parsed.scheme}')
    if parsed.username or parsed.password:
        raise _ImageDownloadError('image URL credentials are not allowed')
    if parsed.hostname is None:
        raise _ImageDownloadError('image URL host is required')
    try:
        port = parsed.port
    except ValueError as exc:
        raise _ImageDownloadError('image URL port is invalid') from exc
    if port is not None and port not in _IMAGE_DOWNLOAD_ALLOWED_PORTS:
        raise _ImageDownloadError(f'image URL port is not allowed: {port}')
    await _validate_public_host(parsed.hostname)


async def _validate_public_host(hostname: str) -> None:
    addresses = await asyncio.to_thread(_resolve_host_addresses, hostname)
    if not addresses:
        raise _ImageDownloadError(f'image URL host did not resolve: {hostname}')
    blocked_addresses = [address for address in addresses if not address.is_global]
    if blocked_addresses:
        blocked = ', '.join(str(address) for address in sorted(blocked_addresses, key=str))
        raise _ImageDownloadError(f'image URL host resolved to a non-public address: {blocked}')


def _resolve_host_addresses(hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    stripped_hostname = hostname.strip('[]')
    try:
        return {ipaddress.ip_address(stripped_hostname)}
    except ValueError:
        pass

    try:
        records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise _ImageDownloadError(f'image URL host did not resolve: {hostname}') from exc
    return {ipaddress.ip_address(record[4][0]) for record in records}


def _redirect_target(current_url: str, response: httpx.Response) -> str:
    location = response.headers.get('location')
    if not location:
        raise _ImageDownloadError('image redirect did not include a location')
    return urljoin(current_url, location)


def _validate_image_response(content_type: str, raw_content_length: str | None) -> None:
    if content_type and not content_type.startswith('image/'):
        raise _ImageDownloadError(f'URL did not return image content: {content_type}')
    content_length = _parse_content_length(raw_content_length)
    if content_length is not None:
        _validate_download_size(content_length)


def _validate_download_size(size_bytes: int) -> None:
    if size_bytes > _IMAGE_DOWNLOAD_MAX_BYTES:
        raise _ImageDownloadError(f'image is too large: {size_bytes} bytes')


def _validate_downloaded_body(path: Path | None, total_bytes: int) -> None:
    if path is None or total_bytes == 0:
        raise _ImageDownloadError('image response body is empty')


def _image_suffix(image_url: str, content_type: str) -> str:
    url_suffix = Path(urlparse(image_url).path).suffix.lower()
    if url_suffix in {'.gif', '.jpg', '.jpeg', '.png', '.webp'}:
        return '.jpg' if url_suffix == '.jpeg' else url_suffix
    return _IMAGE_CONTENT_TYPE_SUFFIXES.get(content_type, '.img')


def _image_filename(image_url: str, suffix: str) -> str:
    filename = Path(urlparse(image_url).path).name
    if filename and filename not in {'.', '..'}:
        return filename
    return f'notification-image{suffix}'
