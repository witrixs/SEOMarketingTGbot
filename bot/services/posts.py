from __future__ import annotations

from typing import Optional, Dict, Any
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from bot.db import Database
from bot.keyboards import post_link_kb

logger = logging.getLogger(__name__)

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


async def send_post_to_all_subscribers(
    *,
    bot: Bot,
    db: Database,
    content_type: str,
    file_id: Optional[str | object],
    text: Optional[str],
    link_override: Optional[str],
    button_text_override: Optional[str] = None,
) -> Dict[str, int]:
    """
    Отправляет пост всем активным подписчикам и возвращает статистику
    Возвращает: {"sent": количество отправленных, "blocked": количество заблокированных}
    """
    batch = 1000
    offset = 0
    sent_count = 0
    blocked_count = 0
    
    while True:
        users = await db.list_active_subscribers(limit=batch, offset=offset)
        if not users:
            break
            
        for u in users:
            user_id = int(u["user_id"]) if isinstance(u["user_id"], (int, str)) else u["user_id"]
            try:
                await send_post_to_chat(
                    bot=bot,
                    db=db,
                    chat_id=user_id,
                    content_type=content_type,
                    file_id=file_id,
                    text=text,
                    link_override=link_override,
                    button_text_override=button_text_override,
                )
                sent_count += 1
            except TelegramForbiddenError:
                # Пользователь заблокировал бота
                await db.set_subscriber_active(user_id, False)
                blocked_count += 1
            except Exception as e:
                logger.warning("Failed to send post to user %s: %s", user_id, e)
                
        offset += batch
    
    return {"sent": sent_count, "blocked": blocked_count} 