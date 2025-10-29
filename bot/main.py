from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from bot.config import load_config
from bot.db import Database
from bot.deps import set_config, set_db
from bot.routers.admin import router as admin_router
from bot.routers.public import router as public_router
from bot.scheduler import start_scheduler


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    set_config(config)

    db = Database(config.database_path)
    await db.init()
    set_db(db)

    # Seed default global link if provided and not set yet
    if config.default_global_link and not await db.get_setting("global_link"):
        await db.set_setting("global_link", config.default_global_link)

    # Seed a default promo post if none exists
    async with db.connection.execute("SELECT COUNT(*) FROM posts") as cur:
        posts_count = (await cur.fetchone())[0]
    if posts_count == 0:
        default_text = (
            "🌟 Ваш гид по бонусам! 🌟\n\n"
            "Узнайте, как получить:\n"
            "🎁 Бонус 500% на первые депозиты\n"
            "💸 30% кэшбэк каждую неделю\n"
            "⚡️ Быстрые выплаты на карту или крипту\n"
            "❤️ Поддержка 24/7\n\n"
            "Оставайтесь с нами, чтобы быть первыми!💰"
        )
        await db.create_post(
            title="Промо",
            content_type="text",
            file_id=None,
            text=default_text,
            link_override=None,
            button_text=None,
        )

    # Seed default destination if provided (legacy, harmless if absent)
    if config.default_destination_chat_id:
        try:
            await db.add_destination(config.default_destination_chat_id, "Default from .env")
        except Exception:
            pass

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(public_router)

    # Start scheduler
    asyncio.create_task(start_scheduler(bot, db))

    try:
        logger.info("Bot polling started")
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "my_chat_member", "chat_join_request"])
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main()) 