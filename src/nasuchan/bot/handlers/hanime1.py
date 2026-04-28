from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from nasuchan.clients import (
    BackendApiConflictError,
    BackendApiError,
    BackendApiUnprocessableError,
)
from nasuchan.services import (
    BackendCommandService,
    build_backend_user_message,
    format_seed_added_message,
)


class Hanime1SeedStates(StatesGroup):
    waiting_for_seed = State()


_REMOVED_SEED_ACTION_MESSAGE = 'This Hanime1 action is no longer available. Fav now supports adding scan targets only.'


async def handle_hanime1_seeds_menu(message: Message) -> None:
    await message.answer('Choose a Hanime1 action:', reply_markup=build_seed_menu_keyboard())


async def handle_hanime1_seed_add_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Hanime1SeedStates.waiting_for_seed)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer('Send the raw Hanime1 scan target text. Use /cancel to clear the active flow.')


async def handle_hanime1_seed_input(
    message: Message,
    state: FSMContext,
    command_service: BackendCommandService,
    logger: logging.Logger,
) -> None:
    raw_target = (message.text or '').strip()
    if not raw_target:
        await message.answer('Scan target input cannot be empty.')
        return
    if raw_target in {'/cancel', '/cancel@nasuchan_bot'} or raw_target.casefold() == 'cancel':
        await state.clear()
        await message.answer('Active bot state cleared.')
        return
    try:
        seed = await command_service.add_hanime1_scan_target(raw_target)
    except BackendApiConflictError:
        await message.answer('That Hanime1 scan target already exists.')
        return
    except BackendApiUnprocessableError:
        await message.answer('Backend could not resolve that Hanime1 scan target.')
        return
    except BackendApiError as exc:
        logger.exception('Failed to add Hanime1 scan target')
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


async def handle_removed_hanime1_seed_action(callback: CallbackQuery) -> None:
    await callback.answer(_REMOVED_SEED_ACTION_MESSAGE)
    if callback.message is None:
        return
    try:
        await callback.message.edit_text('Choose a Hanime1 action:', reply_markup=build_seed_menu_keyboard())
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def build_hanime1_router(command_service: BackendCommandService, logger: logging.Logger | None = None) -> Router:
    hanime1_logger = logger or logging.getLogger(__name__)
    router = Router(name='hanime1')

    @router.message(F.text == 'cancel', Hanime1SeedStates.waiting_for_seed)
    async def cancel_text_handler(message: Message, state: FSMContext) -> None:
        await handle_cancel(message, state)

    @router.message(F.text, Hanime1SeedStates.waiting_for_seed)
    async def seed_input_handler(message: Message, state: FSMContext) -> None:
        await handle_hanime1_seed_input(message, state, command_service, hanime1_logger)

    @router.message(Command('cancel'))
    async def cancel_command_handler(message: Message, state: FSMContext) -> None:
        await handle_cancel(message, state)

    @router.callback_query(F.data == 'seed:add')
    async def add_handler(callback: CallbackQuery, state: FSMContext) -> None:
        await handle_hanime1_seed_add_prompt(callback, state)

    @router.callback_query(F.data == 'seed:list')
    async def removed_list_handler(callback: CallbackQuery) -> None:
        await handle_removed_hanime1_seed_action(callback)

    @router.callback_query(F.data == 'seed:delete')
    async def removed_delete_handler(callback: CallbackQuery) -> None:
        await handle_removed_hanime1_seed_action(callback)

    @router.callback_query(F.data.startswith('seed:page:'))
    async def removed_page_handler(callback: CallbackQuery) -> None:
        await handle_removed_hanime1_seed_action(callback)

    @router.callback_query(F.data.startswith('seed:rm:'))
    async def removed_remove_handler(callback: CallbackQuery) -> None:
        await handle_removed_hanime1_seed_action(callback)

    return router


def build_seed_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='Add scan target', callback_data='seed:add')
    builder.adjust(1)
    return builder.as_markup()
