from __future__ import annotations

import logging
from math import ceil

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from nasuchan.clients import (
    BackendApiConflictError,
    BackendApiError,
    BackendApiNotFoundError,
    BackendApiUnprocessableError,
    FavBackendClient,
    Hanime1Seed,
)
from nasuchan.services import (
    build_backend_user_message,
    format_seed_added_message,
    format_seed_deleted_message,
    format_seed_page_message,
    split_text_chunks,
)

_DELETE_PAGE_SIZE = 10


class Hanime1SeedStates(StatesGroup):
    waiting_for_seed = State()


async def handle_hanime1_seeds_menu(message: Message) -> None:
    await message.answer('Choose a Hanime1 seed action:', reply_markup=build_seed_menu_keyboard())


async def handle_hanime1_seed_list(message: Message, backend_client: FavBackendClient, logger: logging.Logger) -> None:
    try:
        seeds = await backend_client.list_hanime1_seeds()
    except BackendApiError as exc:
        logger.exception('Failed to list Hanime1 seeds')
        await message.answer(build_backend_user_message(exc))
        return
    text = format_seed_page_message(seeds, page=0, page_size=max(len(seeds), 1))
    for chunk in split_text_chunks(text):
        await message.answer(chunk)


async def handle_hanime1_seed_add_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Hanime1SeedStates.waiting_for_seed)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer('Send the raw Hanime1 seed text. Use /cancel to clear the active flow.')


async def handle_hanime1_seed_input(
    message: Message,
    state: FSMContext,
    backend_client: FavBackendClient,
    logger: logging.Logger,
) -> None:
    raw_seed = (message.text or '').strip()
    if not raw_seed:
        await message.answer('Seed input cannot be empty.')
        return
    if raw_seed in {'/cancel', '/cancel@nasuchan_bot'} or raw_seed.casefold() == 'cancel':
        await state.clear()
        await message.answer('Active bot state cleared.')
        return
    try:
        seed = await backend_client.add_hanime1_seed(raw_seed)
    except BackendApiConflictError:
        await message.answer('That Hanime1 seed already exists.')
        return
    except BackendApiUnprocessableError:
        await message.answer('Backend could not resolve that Hanime1 seed.')
        return
    except BackendApiError as exc:
        logger.exception('Failed to add Hanime1 seed')
        await message.answer(build_backend_user_message(exc))
        return
    await state.clear()
    await message.answer(format_seed_added_message(seed))


async def handle_cancel(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer('There is no active bot state to clear.')
        return
    await state.clear()
    await message.answer('Active bot state cleared.')


async def handle_hanime1_seed_delete_page(
    callback: CallbackQuery,
    backend_client: FavBackendClient,
    logger: logging.Logger,
    page: int,
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    try:
        seeds = await backend_client.list_hanime1_seeds()
    except BackendApiError as exc:
        logger.exception('Failed to load Hanime1 seed delete page')
        await callback.message.answer(build_backend_user_message(exc))
        return
    await callback.message.edit_text(
        format_seed_page_message(seeds, page=page, page_size=_DELETE_PAGE_SIZE),
        reply_markup=build_seed_delete_keyboard(seeds, page=page, page_size=_DELETE_PAGE_SIZE),
    )


async def handle_hanime1_seed_delete(
    callback: CallbackQuery,
    backend_client: FavBackendClient,
    logger: logging.Logger,
    video_id: str,
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    try:
        seed = await backend_client.delete_hanime1_seed(video_id)
    except BackendApiNotFoundError:
        await callback.message.answer('That Hanime1 seed does not exist anymore.')
        return
    except BackendApiError as exc:
        logger.exception('Failed to delete Hanime1 seed %s', video_id)
        await callback.message.answer(build_backend_user_message(exc))
        return
    await callback.message.answer(format_seed_deleted_message(seed))


def build_hanime1_router(backend_client: FavBackendClient, logger: logging.Logger | None = None) -> Router:
    hanime1_logger = logger or logging.getLogger(__name__)
    router = Router(name='hanime1')

    @router.message(F.text == 'cancel', Hanime1SeedStates.waiting_for_seed)
    async def cancel_text_handler(message: Message, state: FSMContext) -> None:
        await handle_cancel(message, state)

    @router.message(F.text, Hanime1SeedStates.waiting_for_seed)
    async def seed_input_handler(message: Message, state: FSMContext) -> None:
        await handle_hanime1_seed_input(message, state, backend_client, hanime1_logger)

    @router.message(Command('cancel'))
    async def cancel_command_handler(message: Message, state: FSMContext) -> None:
        await handle_cancel(message, state)

    @router.callback_query(F.data == 'seed:list')
    async def list_handler(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        await callback.answer()
        await handle_hanime1_seed_list(callback.message, backend_client, hanime1_logger)

    @router.callback_query(F.data == 'seed:add')
    async def add_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await handle_hanime1_seed_add_prompt(callback, state)

    @router.callback_query(F.data == 'seed:delete')
    async def delete_menu_handler(callback: CallbackQuery) -> None:
        await handle_hanime1_seed_delete_page(callback, backend_client, hanime1_logger, page=0)

    @router.callback_query(F.data.startswith('seed:page:'))
    async def delete_page_handler(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        page = int(callback.data.split(':', maxsplit=2)[2])
        await handle_hanime1_seed_delete_page(callback, backend_client, hanime1_logger, page=page)

    @router.callback_query(F.data.startswith('seed:rm:'))
    async def delete_handler(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        video_id = callback.data.split(':', maxsplit=2)[2]
        await handle_hanime1_seed_delete(callback, backend_client, hanime1_logger, video_id)

    return router


def build_seed_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='List', callback_data='seed:list')
    builder.button(text='Add', callback_data='seed:add')
    builder.button(text='Delete', callback_data='seed:delete')
    builder.adjust(3)
    return builder.as_markup()


def build_seed_delete_keyboard(seeds: list[Hanime1Seed], *, page: int, page_size: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * page_size
    end = start + page_size
    for seed in seeds[start:end]:
        builder.button(text=f'Delete {seed.video_id}', callback_data=f'seed:rm:{seed.video_id}')

    total_pages = max(ceil(len(seeds) / page_size), 1)
    if total_pages > 1:
        if page > 0:
            builder.button(text='Prev', callback_data=f'seed:page:{page - 1}')
        if page + 1 < total_pages:
            builder.button(text='Next', callback_data=f'seed:page:{page + 1}')

    builder.adjust(1)
    return builder.as_markup()
