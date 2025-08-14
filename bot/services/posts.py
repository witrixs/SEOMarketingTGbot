from __future__ import annotations

from typing import Optional

from aiogram import Bot

from bot.db import Database
from bot.keyboards import post_link_kb


DEFAULT_BUTTON_TEXT = "🎰 Забрать бонус"


async def resolve_post_url(db: Database, link_override: Optional[str]) -> Optional[str]:
    if link_override:
        return link_override
    value = await db.get_setting("global_link")
    return value


async def resolve_button_text(db: Database, button_text_override: Optional[str]) -> str:
    if button_text_override:
        return button_text_override
    global_btn = await db.get_setting("global_button_text")
    return global_btn or DEFAULT_BUTTON_TEXT


async def send_post_to_chat(
    *,
    bot: Bot,
    db: Database,
    chat_id: int,
    content_type: str,
    file_id: Optional[str | object],
    text: Optional[str],
    link_override: Optional[str],
    button_text_override: Optional[str] = None,
) -> None:
    url = await resolve_post_url(db, link_override)
    if url:
        button_text = await resolve_button_text(db, button_text_override)
    else:
        button_text = None

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    if url and button_text:
        kb.button(text=button_text, url=url)
    reply_markup = kb.as_markup()

    if content_type == "text":
        await bot.send_message(chat_id, text or "", reply_markup=reply_markup, disable_web_page_preview=True)
        return

    if content_type == "photo" and file_id:
        await bot.send_photo(chat_id, file_id, caption=text or None, reply_markup=reply_markup)
        return

    if content_type == "animation" and file_id:
        await bot.send_animation(chat_id, file_id, caption=text or None, reply_markup=reply_markup)
        return

    if content_type == "video" and file_id:
        await bot.send_video(chat_id, file_id, caption=text or None, reply_markup=reply_markup)
        return

    await bot.send_message(chat_id, (text or "") + "\n\n(Контент недоступен)", reply_markup=reply_markup) 