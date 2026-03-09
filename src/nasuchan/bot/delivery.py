from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode


async def send_markdown_to_chat(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
) -> None:
    await bot.send_message(
        chat_id,
        markdown,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=disable_web_page_preview,
        disable_notification=disable_notification,
    )
