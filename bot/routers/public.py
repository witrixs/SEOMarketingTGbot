from __future__ import annotations

from pathlib import Path

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

from bot.services.posts import send_post_to_chat
from bot.deps import get_db

router = Router(name="public")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db = get_db()
    user = message.from_user
    if user:
        await db.add_or_update_subscriber(user.id, user.first_name, user.username)

    default_text = (
        "🌟 Ваш гид по бонусам! 🌟\n\n"
        "Узнайте, как получить:\n"
        "🎁 Бонус 500% на первые депозиты\n"
        "💸 30% кэшбэк каждую неделю\n"
        "⚡️ Быстрые выплаты на карту или крипту\n"
        "❤️ Поддержка 24/7\n\n"
        "Оставайтесь с нами, чтобы быть первыми! 🚀 Внесите депозит"
    )

    media_path = Path.cwd() / "media" / "Global-post.png"
    if media_path.exists():
        file = FSInputFile(str(media_path))
        await send_post_to_chat(
            bot=message.bot,
            db=db,
            chat_id=message.chat.id,
            content_type="photo",
            file_id=file,
            text=default_text,
            link_override=None,
            button_text_override=None,
        )
    else:
        await send_post_to_chat(
            bot=message.bot,
            db=db,
            chat_id=message.chat.id,
            content_type="text",
            file_id=None,
            text=default_text,
            link_override=None,
            button_text_override=None,
        ) 