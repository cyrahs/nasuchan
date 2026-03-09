from __future__ import annotations

import logging
from math import ceil
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from nasuchan.clients import AninamerJobRequest, AninamerStatusItem, BackendApiError, JobRequest
from nasuchan.config.settings import PollingSettings
from nasuchan.bot.handlers.hanime1 import build_seed_menu_keyboard
from nasuchan.services import (
    BackendCommandService,
    build_backend_user_message,
    build_help_text,
    format_aggregated_health_message,
    format_aggregated_jobs_message,
    format_aninamer_apply_page,
    format_aninamer_job_request_message,
    format_job_request_message,
    poll_job_request,
    split_text_chunks,
)

_RUN_ALL_TARGET = 'all'
_ANINAMER_APPLY_PAGE_SIZE = 10


async def handle_start(message: Message) -> None:
    await message.answer(build_help_text())


async def handle_health(message: Message, command_service: BackendCommandService, logger: logging.Logger) -> None:
    try:
        snapshots = await command_service.collect_health()
    except Exception:
        logger.exception('Failed to collect backend health')
        await message.answer('Failed to collect backend health.')
        return

    for chunk in split_text_chunks(
        format_aggregated_health_message(snapshots, error_lookup=build_backend_user_message)
    ):
        await message.answer(chunk)


async def handle_jobs(message: Message, command_service: BackendCommandService, logger: logging.Logger) -> None:
    try:
        snapshot = await command_service.collect_jobs()
    except Exception:
        logger.exception('Failed to collect backend jobs')
        await message.answer('Failed to collect backend jobs.')
        return

    for chunk in split_text_chunks(
        format_aggregated_jobs_message(snapshot, error_lookup=build_backend_user_message)
    ):
        await message.answer(chunk)


async def handle_run(message: Message, command_service: BackendCommandService, logger: logging.Logger) -> None:
    backends = command_service.available_run_backends()
    if not backends:
        await message.answer('No runnable backends are currently configured.')
        return
    if len(backends) == 1:
        await _show_run_backend_menu(message, backends[0], command_service, logger)
        return
    await message.answer('Choose a backend:', reply_markup=build_run_backend_keyboard(backends))


async def handle_config(message: Message, *, has_hanime1: bool) -> None:
    if not has_hanime1:
        await message.answer('No runtime config actions are currently available.')
        return
    await message.answer('Choose a config area:', reply_markup=build_config_keyboard())


async def handle_run_backend_callback(
    callback: CallbackQuery,
    command_service: BackendCommandService,
    logger: logging.Logger,
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    backend = callback.data.rsplit(':', maxsplit=1)[1]
    await callback.answer()
    await _show_run_backend_menu(callback.message, backend, command_service, logger, edit_message=True)


async def handle_fav_run_callback(
    callback: CallbackQuery,
    command_service: BackendCommandService,
    polling: PollingSettings,
    logger: logging.Logger,
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    _, _, target = callback.data.split(':', maxsplit=2)
    await callback.answer()
    await safe_edit_message(callback.message, f'Creating request for `{target}`...')
    try:
        request = await command_service.create_fav_job_request(target)
    except BackendApiError as exc:
        logger.exception('Failed to create Fav job request for %s', target)
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    await _poll_request_updates(
        callback.message,
        request_id=request.id,
        backend_client=command_service.fav_client,
        polling=polling,
        logger=logger,
        failure_log='Failed while polling Fav job request %s',
        format_request=format_job_request_message,
    )


async def handle_aninamer_scan_now_callback(
    callback: CallbackQuery,
    command_service: BackendCommandService,
    polling: PollingSettings,
    logger: logging.Logger,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    await safe_edit_message(callback.message, 'Creating Aninamer request for `scan_now`...')
    try:
        request = await command_service.create_aninamer_scan_now_request()
    except BackendApiError as exc:
        logger.exception('Failed to create Aninamer scan_now request')
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    await _poll_request_updates(
        callback.message,
        request_id=request.id,
        backend_client=command_service.aninamer_client,
        polling=polling,
        logger=logger,
        failure_log='Failed while polling Aninamer request %s',
        format_request=format_aninamer_job_request_message,
    )


async def handle_aninamer_apply_page(
    callback: CallbackQuery,
    command_service: BackendCommandService,
    logger: logging.Logger,
    *,
    page: int,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    try:
        items = await command_service.list_aninamer_planned_jobs()
    except BackendApiError as exc:
        logger.exception('Failed to list Aninamer planned jobs')
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    if not items:
        await safe_edit_message(callback.message, 'No Aninamer jobs are currently in planned state.')
        return

    await callback.message.edit_text(
        format_aninamer_apply_page(items, page=page, page_size=_ANINAMER_APPLY_PAGE_SIZE),
        reply_markup=build_aninamer_apply_keyboard(
            items,
            page=page,
            page_size=_ANINAMER_APPLY_PAGE_SIZE,
        ),
    )


async def handle_aninamer_apply_job_callback(
    callback: CallbackQuery,
    command_service: BackendCommandService,
    polling: PollingSettings,
    logger: logging.Logger,
    *,
    job_id: int,
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    await safe_edit_message(callback.message, f'Creating Aninamer request for `apply_job` on `#{job_id}`...')
    try:
        request = await command_service.create_aninamer_apply_job_request(job_id)
    except BackendApiError as exc:
        logger.exception('Failed to create Aninamer apply request for job %s', job_id)
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    await _poll_request_updates(
        callback.message,
        request_id=request.id,
        backend_client=command_service.aninamer_client,
        polling=polling,
        logger=logger,
        failure_log='Failed while polling Aninamer request %s',
        format_request=format_aninamer_job_request_message,
    )


async def handle_config_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    try:
        await callback.message.edit_text('Choose a Hanime1 action:', reply_markup=build_seed_menu_keyboard())
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def build_commands_router(
    command_service: BackendCommandService,
    polling: PollingSettings,
    logger: logging.Logger | None = None,
) -> Router:
    command_logger = logger or logging.getLogger(__name__)
    router = Router(name='commands')

    @router.message(Command('start'))
    async def start_handler(message: Message) -> None:
        await handle_start(message)

    @router.message(Command('health'))
    async def health_handler(message: Message) -> None:
        await handle_health(message, command_service, command_logger)

    @router.message(Command('jobs'))
    async def jobs_handler(message: Message) -> None:
        await handle_jobs(message, command_service, command_logger)

    @router.message(Command('run'))
    async def run_handler(message: Message) -> None:
        await handle_run(message, command_service, command_logger)

    @router.message(Command('config'))
    async def config_handler(message: Message) -> None:
        await handle_config(message, has_hanime1=command_service.has_fav)

    @router.callback_query(F.data.startswith('run:backend:'))
    async def run_backend_callback_handler(callback: CallbackQuery) -> None:
        await handle_run_backend_callback(callback, command_service, command_logger)

    @router.callback_query(F.data.startswith('run:fav:'))
    async def fav_run_callback_handler(callback: CallbackQuery) -> None:
        await handle_fav_run_callback(callback, command_service, polling, command_logger)

    @router.callback_query(F.data == 'run:aninamer:scan_now')
    async def aninamer_scan_now_handler(callback: CallbackQuery) -> None:
        await handle_aninamer_scan_now_callback(callback, command_service, polling, command_logger)

    @router.callback_query(F.data == 'run:aninamer:apply')
    async def aninamer_apply_menu_handler(callback: CallbackQuery) -> None:
        await handle_aninamer_apply_page(callback, command_service, command_logger, page=0)

    @router.callback_query(F.data.startswith('run:aninamer:apply:page:'))
    async def aninamer_apply_page_handler(callback: CallbackQuery) -> None:
        if callback.data is None:
            await callback.answer()
            return
        page = int(callback.data.rsplit(':', maxsplit=1)[1])
        await handle_aninamer_apply_page(callback, command_service, command_logger, page=page)

    @router.callback_query(F.data.startswith('run:aninamer:apply:job:'))
    async def aninamer_apply_job_handler(callback: CallbackQuery) -> None:
        if callback.data is None:
            await callback.answer()
            return
        job_id = int(callback.data.rsplit(':', maxsplit=1)[1])
        await handle_aninamer_apply_job_callback(callback, command_service, polling, command_logger, job_id=job_id)

    if command_service.has_fav:
        @router.callback_query(F.data == 'config:hanime1')
        async def config_callback_handler(callback: CallbackQuery) -> None:
            await handle_config_callback(callback)

    return router


def build_run_backend_keyboard(backends: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for backend in backends:
        builder.button(text=backend, callback_data=f'run:backend:{backend}')
    builder.adjust(2)
    return builder.as_markup()


def build_fav_run_keyboard(job_keys: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for job_key in job_keys:
        builder.button(text=job_key, callback_data=f'run:fav:{job_key}')
    builder.button(text=_RUN_ALL_TARGET, callback_data=f'run:fav:{_RUN_ALL_TARGET}')
    builder.adjust(2)
    return builder.as_markup()


def build_aninamer_action_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='scan_now', callback_data='run:aninamer:scan_now')
    builder.button(text='apply_job', callback_data='run:aninamer:apply')
    builder.adjust(2)
    return builder.as_markup()


def build_aninamer_apply_keyboard(
    items: list[AninamerStatusItem],
    *,
    page: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    end = start + page_size
    for item in items[start:end]:
        builder.button(
            text=f'#{item.job_id} {item.series_name}',
            callback_data=f'run:aninamer:apply:job:{item.job_id}',
        )

    total_pages = max(ceil(len(items) / page_size), 1)
    if total_pages > 1:
        if page > 0:
            builder.button(text='Prev', callback_data=f'run:aninamer:apply:page:{page - 1}')
        if page + 1 < total_pages:
            builder.button(text='Next', callback_data=f'run:aninamer:apply:page:{page + 1}')

    builder.adjust(1)
    return builder.as_markup()


def build_config_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='hanime1', callback_data='config:hanime1')
    builder.adjust(1)
    return builder.as_markup()


async def _show_run_backend_menu(
    message: Message,
    backend: str,
    command_service: BackendCommandService,
    logger: logging.Logger,
    *,
    edit_message: bool = False,
) -> None:
    if backend == 'fav':
        await _show_fav_run_menu(message, command_service, logger, edit_message=edit_message)
        return
    if backend == 'aninamer':
        text = 'Choose an Aninamer action:'
        if edit_message:
            await safe_edit_message(message, text, reply_markup=build_aninamer_action_keyboard())
        else:
            await message.answer(text, reply_markup=build_aninamer_action_keyboard())
        return
    msg = f'Unsupported backend: {backend}'
    raise ValueError(msg)


async def _show_fav_run_menu(
    message: Message,
    command_service: BackendCommandService,
    logger: logging.Logger,
    *,
    edit_message: bool = False,
) -> None:
    try:
        jobs = await command_service.list_fav_jobs()
    except BackendApiError as exc:
        logger.exception('Failed to list Fav jobs for /run')
        text = build_backend_user_message(exc)
        if edit_message:
            await safe_edit_message(message, text)
        else:
            await message.answer(text)
        return
    enabled_jobs = [job for job in jobs if job.enabled]
    if not enabled_jobs:
        text = 'No enabled jobs are currently available.'
        if edit_message:
            await safe_edit_message(message, text)
        else:
            await message.answer(text)
        return
    if edit_message:
        await safe_edit_message(
            message,
            'Choose a Fav job to trigger:',
            reply_markup=build_fav_run_keyboard([job.key for job in enabled_jobs]),
        )
        return
    await message.answer(
        'Choose a Fav job to trigger:',
        reply_markup=build_fav_run_keyboard([job.key for job in enabled_jobs]),
    )


async def _poll_request_updates(
    message: Message,
    *,
    request_id: int,
    backend_client: Any,
    polling: PollingSettings,
    logger: logging.Logger,
    failure_log: str,
    format_request: Any,
) -> None:
    if backend_client is None:
        msg = 'Backend client is not configured.'
        raise RuntimeError(msg)

    last_text = ''

    async def on_update(current_request: JobRequest | AninamerJobRequest) -> None:
        nonlocal last_text
        current_text = format_request(current_request)
        if current_text == last_text:
            return
        last_text = current_text
        await safe_edit_message(message, current_text)

    try:
        result = await poll_job_request(
            backend_client,
            request_id,
            interval_seconds=polling.control_poll_interval_seconds,
            timeout_seconds=polling.control_poll_timeout_seconds,
            on_update=on_update,
        )
    except BackendApiError as exc:
        logger.exception(failure_log, request_id)
        await safe_edit_message(message, build_backend_user_message(exc))
        return

    await safe_edit_message(
        message,
        format_request(result.request, timed_out=result.timed_out),
    )


async def safe_edit_message(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise
