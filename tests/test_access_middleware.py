from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from nasuchan.bot.middleware import AdminChatMiddleware


def build_message(chat_id: int) -> Message:
    return Message.model_validate(
        {
            'message_id': 1,
            'date': 1_709_900_000,
            'chat': {'id': chat_id, 'type': 'private'},
            'text': '/jobs',
        }
    )


@pytest.mark.asyncio
async def test_unauthorized_message_is_blocked_before_handler_runs() -> None:
    middleware = AdminChatMiddleware(admin_chat_id=123)
    handler = AsyncMock()

    result = await middleware(handler, build_message(chat_id=999), {})

    assert result is None
    handler.assert_not_awaited()
