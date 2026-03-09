from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from nasuchan.clients import BackendApiError, FavBackendClient
from nasuchan.config.settings import PollingSettings
from nasuchan.bot.handlers.hanime1 import build_seed_menu_keyboard
from nasuchan.services import (
    NotificationDeliveryService,
    build_backend_user_message,
    build_help_text,
    format_delivery_report,
    format_health_message,
    format_job_request_message,
    format_jobs_message,
    poll_job_request,
)

_RUN_ALL_TARGET = 'all'


async def handle_start(message: Message) -> None:
    await message.answer(build_help_text())


async def handle_health(message: Message, backend_client: FavBackendClient, logger: logging.Logger) -> None:
    try:
        status = await backend_client.health()
    except BackendApiError as exc:
        logger.exception('Failed to fetch backend health')
        await message.answer(build_backend_user_message(exc))
        return
    await message.answer(format_health_message(status))


async def handle_jobs(message: Message, backend_client: FavBackendClient, logger: logging.Logger) -> None:
    try:
        jobs = await backend_client.list_jobs()
    except BackendApiError as exc:
        logger.exception('Failed to list jobs')
        await message.answer(build_backend_user_message(exc))
        return
    await message.answer(format_jobs_message(jobs))


async def handle_run(message: Message, backend_client: FavBackendClient, logger: logging.Logger) -> None:
    try:
        jobs = await backend_client.list_jobs()
    except BackendApiError as exc:
        logger.exception('Failed to list jobs for /run')
        await message.answer(build_backend_user_message(exc))
        return
    enabled_jobs = [job for job in jobs if job.enabled]
    if not enabled_jobs:
        await message.answer('No enabled jobs are currently available.')
        return
    keyboard = build_run_keyboard([job.key for job in enabled_jobs])
    await message.answer('Choose a job to trigger:', reply_markup=keyboard)


async def handle_config(message: Message) -> None:
    await message.answer('Choose a config area:', reply_markup=build_config_keyboard())


async def handle_run_callback(
    callback: CallbackQuery,
    backend_client: FavBackendClient,
    polling: PollingSettings,
    logger: logging.Logger,
) -> None:
    if callback.message is None or callback.data is None:
        await callback.answer()
        return
    _, target = callback.data.split(':', maxsplit=1)
    await callback.answer()
    await safe_edit_message(callback.message, f'Creating request for `{target}`...')
    try:
        request = await backend_client.create_job_request(target)
    except BackendApiError as exc:
        logger.exception('Failed to create job request for %s', target)
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    last_text = ''

    async def on_update(current_request) -> None:  # noqa: ANN001
        nonlocal last_text
        current_text = format_job_request_message(current_request)
        if current_text == last_text:
            return
        last_text = current_text
        await safe_edit_message(callback.message, current_text)

    try:
        result = await poll_job_request(
            backend_client,
            request.id,
            interval_seconds=polling.control_poll_interval_seconds,
            timeout_seconds=polling.control_poll_timeout_seconds,
            on_update=on_update,
        )
    except BackendApiError as exc:
        logger.exception('Failed while polling job request %s', request.id)
        await safe_edit_message(callback.message, build_backend_user_message(exc))
        return

    final_text = format_job_request_message(result.request, timed_out=result.timed_out)
    await safe_edit_message(callback.message, final_text)


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


async def handle_notifications(
    message: Message,
    delivery_service: NotificationDeliveryService,
    logger: logging.Logger,
) -> None:
    try:
        report = await delivery_service.deliver_once()
    except BackendApiError as exc:
        logger.exception('Failed to run manual notification delivery')
        await message.answer(build_backend_user_message(exc))
        return
    await message.answer(format_delivery_report(report.fetched, report.delivered, report.failed, report.acked))


def build_commands_router(
    backend_client: FavBackendClient,
    polling: PollingSettings,
    delivery_service: NotificationDeliveryService,
    logger: logging.Logger | None = None,
) -> Router:
    command_logger = logger or logging.getLogger(__name__)
    router = Router(name='commands')

    @router.message(Command('start'))
    async def start_handler(message: Message) -> None:
        await handle_start(message)

    @router.message(Command('health'))
    async def health_handler(message: Message) -> None:
        await handle_health(message, backend_client, command_logger)

    @router.message(Command('jobs'))
    async def jobs_handler(message: Message) -> None:
        await handle_jobs(message, backend_client, command_logger)

    @router.message(Command('run'))
    async def run_handler(message: Message) -> None:
        await handle_run(message, backend_client, command_logger)

    @router.message(Command('config'))
    async def config_handler(message: Message) -> None:
        await handle_config(message)

    @router.callback_query(F.data.startswith('run:'))
    async def run_callback_handler(callback: CallbackQuery) -> None:
        await handle_run_callback(callback, backend_client, polling, command_logger)

    @router.callback_query(F.data == 'config:hanime1')
    async def config_callback_handler(callback: CallbackQuery) -> None:
        await handle_config_callback(callback)

    @router.message(Command('notifications'))
    async def notifications_handler(message: Message) -> None:
        await handle_notifications(message, delivery_service, command_logger)

    return router


def build_run_keyboard(job_keys: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for job_key in job_keys:
        builder.button(text=job_key, callback_data=f'run:{job_key}')
    builder.button(text=_RUN_ALL_TARGET, callback_data=f'run:{_RUN_ALL_TARGET}')
    builder.adjust(2)
    return builder.as_markup()


def build_config_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='hanime1', callback_data='config:hanime1')
    builder.adjust(1)
    return builder.as_markup()


async def safe_edit_message(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise
