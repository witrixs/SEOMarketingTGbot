from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from bot.db import Database
from bot.services.posts import send_post_to_chat


logger = logging.getLogger(__name__)


async def _send_post_to_all_subscribers(bot: Bot, db: Database, post: dict) -> int:
    batch = 1000
    offset = 0
    success_total = 0
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
                    content_type=post["content_type"],
                    file_id=post["file_id"],
                    text=post["text"],
                    link_override=post["link_override"],
                    button_text_override=post.get("button_text"),
                )
                success_total += 1
            except TelegramForbiddenError:
                await db.set_subscriber_active(user_id, False)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to send post %s to user %s: %s", post["id"], user_id, e)
        offset += batch
    if success_total:
        await db.increment_post_delivery(post["id"], success_total)
    return success_total


async def _run_due_schedules(bot: Bot, db: Database, now_ts: int) -> None:
    due = await db.list_due_schedules(now_ts)
    if not due:
        return
    for item in due:
        schedule_id = item["id"]
        post_id = item["post_id"]
        repeat_interval = item["repeat_interval"]
        post = await db.get_post(post_id)
        if not post:
            await db.mark_schedule_after_run(schedule_id, None)
            continue
        await _send_post_to_all_subscribers(bot, db, post)
        await db.mark_schedule_after_run(schedule_id, repeat_interval)


def _current_local_minute() -> tuple[int, int, int, int]:
    now = datetime.now()
    wday = (now.weekday())  # Monday=0 .. Sunday=6
    return wday, now.hour, now.minute, now.year * 10000 + now.month * 100 + now.day


async def _run_weekly(bot: Bot, db: Database) -> None:
    wday, hour, minute, ymd = _current_local_minute()
    due = await db.list_weekly_due(wday=wday, hour=hour, minute=minute, today_ymd=ymd)
    for item in due:
        post = await db.get_post(item["post_id"])
        if not post:
            await db.mark_weekly_ran(item["id"], ymd)
            continue
        await _send_post_to_all_subscribers(bot, db, post)
        await db.mark_weekly_ran(item["id"], ymd)


async def start_scheduler(bot: Bot, db: Database) -> None:
    logger.info("Scheduler started")
    last_weekly_check_minute = None
    while True:
        try:
            now_ts = int(time.time())
            await _run_due_schedules(bot, db, now_ts)

            # weekly check once per minute
            now_minute = now_ts // 60
            if last_weekly_check_minute != now_minute:
                last_weekly_check_minute = now_minute
                await _run_weekly(bot, db)
        except Exception as e:  # noqa: BLE001
            logger.exception("Scheduler loop error: %s", e)
        await asyncio.sleep(5) 