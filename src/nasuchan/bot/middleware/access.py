from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class AdminChatMiddleware(BaseMiddleware):
    def __init__(self, admin_chat_id: int, logger: logging.Logger | None = None) -> None:
        self._admin_chat_id = admin_chat_id
        self._logger = logger or logging.getLogger(__name__)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = self._extract_chat_id(event)
        if chat_id is None or chat_id == self._admin_chat_id:
            return await handler(event, data)

        self._logger.warning('Rejected update from unauthorized chat %s', chat_id)
        if isinstance(event, CallbackQuery):
            await event.answer('Unauthorized.', show_alert=False)
        return None

    @staticmethod
    def _extract_chat_id(event: TelegramObject) -> int | None:
        if isinstance(event, Message):
            return event.chat.id
        if isinstance(event, CallbackQuery) and event.message is not None:
            return event.message.chat.id
        return None
