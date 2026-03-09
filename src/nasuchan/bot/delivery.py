from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode

_TELEGRAM_CAPTION_LIMIT = 1024


async def send_markdown_to_chat(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    image_url: str = '',
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
) -> None:
    normalized_image_url = image_url.strip()
    if not normalized_image_url:
        await bot.send_message(
            chat_id,
            markdown,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=disable_web_page_preview,
            disable_notification=disable_notification,
        )
        return

    if len(markdown) <= _TELEGRAM_CAPTION_LIMIT:
        await bot.send_photo(
            chat_id,
            normalized_image_url,
            caption=markdown,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_notification=disable_notification,
        )
        return

    await bot.send_photo(
        chat_id,
        normalized_image_url,
        disable_notification=disable_notification,
    )
    await bot.send_message(
        chat_id,
        markdown,
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=disable_web_page_preview,
        disable_notification=disable_notification,
    )
