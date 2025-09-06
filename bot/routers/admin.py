from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Optional, Set, List
import pytz

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import (
    admin_main_kb,
    post_actions_kb,
    schedule_repeat_kb,
    back_kb,
    stats_kb,
    schedule_mode_kb,
    weekly_days_kb,
    posts_page_kb,
)
from bot.services.posts import send_post_to_chat, send_post_to_all_subscribers
from bot.deps import get_db, get_config

def html_to_markdown(text):
    """Конвертирует HTML теги в Markdown форматирование"""
    if not text:
        return text
    import re
    # Заменяем HTML теги на Markdown
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text)
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text)
    text = re.sub(r'<u>(.*?)</u>', r'_\1_', text)
    text = re.sub(r'<s>(.*?)</s>', r'~~\1~~', text)
    return text

router = Router(name="admin")

PAGE_SIZE = 2
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MOSCOW_TZ = pytz.timezone('Europe/Moscow')


def to_moscow_time(timestamp: int) -> datetime:
    """Конвертирует timestamp в московское время"""
    return datetime.fromtimestamp(timestamp, tz=MOSCOW_TZ)


# Кэш для статистики
_stats_cache = {
    "total_users": None,
    "today_users": None,
    "last_update": None
}


async def get_stats_data(db, force_refresh: bool = False):
    """Получить данные статистики с кэшированием"""
    global _stats_cache
    
    # Если это принудительное обновление или кэш пустой, обновляем данные
    if force_refresh or _stats_cache["total_users"] is None:
        start_of_day = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        today_users = await db.count_new_subscribers_since(start_of_day)
        total_users = await db.count_all_subscribers()
        
        _stats_cache = {
            "total_users": total_users,
            "today_users": today_users,
            "last_update": time.time()
        }
        
        return total_users, today_users, True  # True означает, что данные обновлены
    
    # Если кэш есть, проверяем только количество пользователей (быстрая проверка)
    start_of_day = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    current_today_users = await db.count_new_subscribers_since(start_of_day)
    current_total_users = await db.count_all_subscribers()
    
    # Если данные не изменились, возвращаем кэшированные
    if (current_total_users == _stats_cache["total_users"] and 
        current_today_users == _stats_cache["today_users"]):
        return _stats_cache["total_users"], _stats_cache["today_users"], False  # False означает, что данные не изменились
    
    # Если данные изменились, обновляем кэш
    _stats_cache = {
        "total_users": current_total_users,
        "today_users": current_today_users,
        "last_update": time.time()
    }
    
    return current_total_users, current_today_users, True


class CreatePostFSM(StatesGroup):
    waiting_for_title = State()
    waiting_for_content = State()
    waiting_for_link = State()
    waiting_for_button_text = State()
    waiting_for_save_or_send = State()
    waiting_for_publish = State()


class EditPostFSM(StatesGroup):
    waiting_for_field = State()
    waiting_for_value = State()


class ScheduleFSM(StatesGroup):
    waiting_for_mode = State()
    waiting_for_datetime = State()          # one-off
    waiting_for_repeat = State()            # one-off
    weekly_select_days = State()
    weekly_time = State()
    edit_weekly_select_days = State()       # для редактирования
    edit_weekly_time = State()              # для редактирования


class GlobalLinkFSM(StatesGroup):
    waiting_for_link = State()


class GlobalButtonTextFSM(StatesGroup):
    waiting_for_text = State()


class FastPostFSM(StatesGroup):
    waiting_for_title = State()
    waiting_for_content = State()
    waiting_for_link = State()
    waiting_for_button_text = State()


class SchedulePostFSM(StatesGroup):
    waiting_for_title = State()
    waiting_for_content = State()
    waiting_for_link = State()
    waiting_for_button_text = State()
    waiting_for_datetime = State()
    waiting_for_repeat = State()
    weekly_select_days = State()
    waiting_for_weekly_time = State()


# Helpers
async def _is_admin(message: Message) -> bool:
    config = get_config()
    return message.from_user and message.from_user.id in config.admin_ids


async def _ensure_admin(callback: CallbackQuery) -> bool:
    config = get_config()
    return callback.from_user and callback.from_user.id in config.admin_ids


def _format_days_mask(mask: int) -> str:
    if mask == 0:
        return "—"
    if mask == 0b1111111:
        return "Ежедневно"
    parts: List[str] = []
    for i, name in enumerate(WEEKDAY_NAMES):
        if mask & (1 << i):
            parts.append(name)
    return ", ".join(parts) if parts else "—"


def _format_repeat_interval(seconds: Optional[int]) -> str:
    if not seconds:
        return "без повтора"
    # Prefer hours if divisible
    if seconds % 3600 == 0:
        return f"каждые {seconds // 3600} ч"
    if seconds % 60 == 0:
        return f"каждые {seconds // 60} мин"
    return f"каждые {seconds} сек"


@router.message(Command("admin"))
async def admin_entry(message: Message) -> None:
    if not await _is_admin(message):
        return
    await message.answer("Админ-меню:", reply_markup=admin_main_kb())


@router.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.clear()
    await cb.message.edit_text("Админ-меню:", reply_markup=admin_main_kb())
    await cb.answer()


# Posts list pagination
async def _send_posts_page(cb: CallbackQuery, page: int) -> None:
    db = get_db()
    offset = page * PAGE_SIZE
    posts = await db.list_posts(limit=PAGE_SIZE, offset=offset)
    if not posts and page > 0:
        page = 0
        offset = 0
        posts = await db.list_posts(limit=PAGE_SIZE, offset=offset)
    has_prev = page > 0
    next_posts = await db.list_posts(limit=PAGE_SIZE, offset=offset + PAGE_SIZE)
    has_next = len(next_posts) > 0

    if not posts:
        await cb.message.edit_text("Постов пока нет.", reply_markup=admin_main_kb())
        await cb.answer()
        return

    lines = []
    for p in posts:
        title = p.get("title") or "(без названия)"
        text = p.get("text") or "(без текста)"
        lines.append(f"ID {p['id']} • {p['content_type']}\n{title}\n{text[:160]}")

    await cb.message.edit_text("\n\n".join(lines), reply_markup=posts_page_kb(posts, page, has_prev, has_next))
    await cb.answer()


@router.callback_query(F.data == "admin:list_posts")
async def list_posts_root(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    await _send_posts_page(cb, page=0)


@router.callback_query(F.data.startswith("admin:list_posts:page:"))
async def list_posts_page(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    page = int(cb.data.split(":")[-1])
    await _send_posts_page(cb, page=page)


@router.callback_query(F.data.startswith("admin:open_post:"))
async def open_post(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    _, _, post_id_str, page_str = cb.data.split(":")
    post_id = int(post_id_str)
    back_page = int(page_str)
    db = get_db()
    p = await db.get_post(post_id)
    if not p:
        await cb.answer("Пост не найден", show_alert=True)
        return
    title = p.get("title") or "(без названия)"
    text = p.get("text") or "(без текста)"
    btn = p.get("button_text") or "(глобальная/деф.)"

    # collect schedules for this post
    oneoff_lines: List[str] = []
    async with db.connection.execute(
        "SELECT next_run_at, repeat_interval, is_paused FROM schedules WHERE is_deleted = 0 AND post_id = ? ORDER BY next_run_at ASC LIMIT 5",
        (post_id,),
    ) as cur:
        rows = await cur.fetchall()
        for row in rows:
            tm = to_moscow_time(row[0]).strftime("%Y-%m-%d %H:%M")
            rep = _format_repeat_interval(row[1])
            status = "⏸" if row[2] else "▶️"
            oneoff_lines.append(f"{status} {tm} ({rep})")

    weekly_lines: List[str] = []
    async with db.connection.execute(
        "SELECT hour, minute, days_mask, is_paused FROM weekly_schedules WHERE post_id = ? ORDER BY hour, minute",
        (post_id,),
    ) as cur:
        rows = await cur.fetchall()
        for row in rows:
            tm = f"{row[0]:02d}:{row[1]:02d}"
            days = _format_days_mask(row[2])
            status = "⏸" if row[3] else "▶️"
            weekly_lines.append(f"{status} {days} в {tm}")

    schedules_block = ""
    if oneoff_lines or weekly_lines:
        schedules_block = "\n\nРасписания:\n"
        if oneoff_lines:
            schedules_block += "Разовые:\n" + "\n".join(oneoff_lines)
        if weekly_lines:
            if oneoff_lines:
                schedules_block += "\n"
            schedules_block += "Еженедельные:\n" + "\n".join(weekly_lines)

    await cb.message.edit_text(
        f"ID {p['id']} • {p['content_type']}\n{title}\n{text[:1000]}\nКнопка: {btn}{schedules_block}",
        reply_markup=post_actions_kb(p["id"], back_page=back_page),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("post:back_to_list:"))
async def back_to_list(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    page = int(cb.data.split(":")[2])
    await _send_posts_page(cb, page=page)


@router.callback_query(F.data == "admin:create_post")
async def admin_create_post(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await cb.message.edit_text("Введите заголовок поста (для админ-списка):", reply_markup=back_kb())
    await state.set_state(CreatePostFSM.waiting_for_title)
    await cb.answer()


@router.message(CreatePostFSM.waiting_for_title)
async def receive_post_title(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    await state.update_data(title=(message.text or "").strip() or None)
    await state.set_state(CreatePostFSM.waiting_for_content)
    await message.answer("Отправьте контент поста: текст, фото, GIF или видео. Подпись используется как текст.", reply_markup=back_kb())


@router.message(CreatePostFSM.waiting_for_content)
async def receive_post_content(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    content_type: Optional[str] = None
    file_id: Optional[str] = None
    text: Optional[str] = None

    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        text = message.caption or None
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
        text = message.caption or None
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
        text = message.caption or None
    elif message.text:
        content_type = "text"
        file_id = None
        text = message.text

    if not content_type:
        await message.answer("Не удалось распознать контент. Пришлите текст, фото, GIF или видео.", reply_markup=back_kb())
        return

    await state.update_data(content_type=content_type, file_id=file_id, text=text)
    await state.set_state(CreatePostFSM.waiting_for_link)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="createpost:skip_link")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await message.answer("Укажите индивидуальную ссылку или нажмите 'Пропустить' для использования глобальной:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "createpost:skip_link")
async def skip_link(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.update_data(link_override=None)
    await state.set_state(CreatePostFSM.waiting_for_button_text)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="createpost:skip_button")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await cb.message.edit_text("Укажите текст кнопки или нажмите 'Пропустить' для использования глобального/дефолтного:", reply_markup=kb.as_markup())
    await cb.answer("Ссылка пропущена")

@router.message(CreatePostFSM.waiting_for_link)
async def receive_post_link(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    link_override = message.text.strip() if message.text else None
    await state.update_data(link_override=link_override)
    await state.set_state(CreatePostFSM.waiting_for_button_text)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="createpost:skip_button")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await message.answer("Укажите текст кнопки или нажмите 'Пропустить' для использования глобального/дефолтного:", reply_markup=kb.as_markup())


@router.callback_query(F.data == "createpost:skip_button")
async def skip_button(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.update_data(button_text=None)
    data = await state.get_data()
    # Формируем предпросмотр поста
    # Конвертируем HTML в Markdown для предпросмотра
    
    preview = f"*Предпросмотр поста:*\n"
    preview += f"*Заголовок:* {data.get('title') or '(нет)'}\n"
    preview += f"*Текст:* {html_to_markdown(data.get('text')) or '(нет)'}\n"
    preview += f"*Кнопка:* (глобальная/дефолтная)"
    await state.set_state(CreatePostFSM.waiting_for_publish)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Выложить пост", callback_data="createpost:publish_now")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await cb.message.edit_text(preview, reply_markup=kb.as_markup(), parse_mode="Markdown")
    await cb.answer("Кнопка пропущена")

@router.message(CreatePostFSM.waiting_for_button_text)
async def receive_button_text(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    btn_text = message.text.strip() if message.text else None
    await state.update_data(button_text=btn_text)
    data = await state.get_data()
    # Формируем предпросмотр поста
    # Конвертируем HTML в Markdown для предпросмотра
    
    preview = f"*Предпросмотр поста:*\n"
    preview += f"*Заголовок:* {data.get('title') or '(нет)'}\n"
    preview += f"*Текст:* {html_to_markdown(data.get('text')) or '(нет)'}\n"
    preview += f"*Кнопка:* {btn_text or '(глобальная/дефолтная)'}"
    await state.set_state(CreatePostFSM.waiting_for_publish)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Выложить пост", callback_data="createpost:publish_now")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await message.answer(preview, reply_markup=kb.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "createpost:publish_now")
async def publish_new_post(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    data = await state.get_data()
    db = get_db()
    
    # Показываем начальное сообщение о начале рассылки
    progress_message = await cb.message.answer(
        "🚀 Начинаем рассылку...\n\n"
        "✅ Отправлено: 0 пользователям\n"
        "🚫 Заблокировали бота: 0 пользователей",
        reply_markup=admin_main_kb()
    )
    
    # Отправляем пост всем подписчикам
    stats = await send_post_to_all_subscribers(
        bot=cb.bot,
        db=db,
        content_type=data["content_type"],
        file_id=data.get("file_id"),
        text=data.get("text"),
        link_override=data.get("link_override"),
        button_text_override=data.get("button_text"),
        progress_message=progress_message,
    )
    
    await state.clear()
    
    # Обновляем сообщение с финальной статистикой
    final_message = (
        f"📊 Рассылка завершена!\n\n"
        f"✅ Отправлено: {stats['sent']} пользователям\n"
        f"🚫 Заблокировали бота: {stats['blocked']} пользователей\n\n"
        f"📢 Пост отправлен всем пользователям!"
    )
    
    await progress_message.edit_text(final_message, reply_markup=admin_main_kb(), parse_mode='Markdown')
    await cb.answer("Пост выложен!", show_alert=True) 


@router.callback_query(F.data.startswith("post:schedule:"))
async def schedule_post(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    post_id = int(cb.data.split(":")[2])
    await state.set_state(ScheduleFSM.waiting_for_mode)
    await state.update_data(post_id=post_id)
    await cb.message.edit_text("Выберите режим расписания:", reply_markup=schedule_mode_kb())
    await cb.answer()


@router.callback_query(ScheduleFSM.waiting_for_mode, F.data == "sched:mode:oneoff")
async def sched_oneoff_mode(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.set_state(ScheduleFSM.waiting_for_datetime)
    await cb.message.edit_text("Укажите дату и время в формате YYYY-MM-DD HH:MM (по местному времени сервера)", reply_markup=back_kb())
    await cb.answer()


@router.callback_query(ScheduleFSM.waiting_for_mode, F.data == "sched:mode:weekly")
async def sched_weekly_mode(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.update_data(weekly_days=set())
    await state.set_state(ScheduleFSM.weekly_select_days)
    await cb.message.edit_text("Выберите дни недели (нажимайте, затем 'Готово'):", reply_markup=weekly_days_kb())
    await cb.answer()


@router.callback_query(ScheduleFSM.weekly_select_days, F.data.startswith("wday:"))
async def sched_weekly_select_days(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    _, _, value = cb.data.partition(":")
    if value == "done":
        data = await state.get_data()
        days: Set[int] = data.get("weekly_days") or set()
        if not days:
            await cb.answer("Выберите хотя бы один день", show_alert=True)
            return
        await state.set_state(ScheduleFSM.weekly_time)
        await cb.message.edit_text("Укажите время в формате HH:MM:", reply_markup=back_kb())
        await cb.answer()
        return
    try:
        d = int(value)
        if d < 0 or d > 6:
            raise ValueError
    except Exception:
        await cb.answer("Неверный день", show_alert=True)
        return
    data = await state.get_data()
    days: Set[int] = data.get("weekly_days") or set()
    if d in days:
        days.remove(d)
    else:
        days.add(d)
    await state.update_data(weekly_days=days)
    
    # Обновляем клавиатуру с новыми галочками
    try:
        await cb.message.edit_reply_markup(reply_markup=weekly_days_kb(selected_days=days))
        # Небольшая задержка чтобы избежать flood control
        import asyncio
        await asyncio.sleep(0.2)
    except Exception as e:
        # Если flood control - просто пропускаем обновление клавиатуры
        if "Flood control" in str(e) or "Too Many Requests" in str(e):
            logger.debug("Skipping keyboard update due to flood control")
        else:
            logger.warning("Failed to update keyboard: %s", e)
    await cb.answer("OK")


@router.message(ScheduleFSM.weekly_time)
async def sched_weekly_time_enter(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    try:
        hh, mm = (message.text or "").strip().split(":", 1)
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except Exception:
        await message.answer("Неверное время. Пример: 12:30", reply_markup=back_kb())
        return
    data = await state.get_data()
    post_id = int(data["post_id"])
    days: Set[int] = data.get("weekly_days") or set()
    mask = 0
    for d in days:
        mask |= (1 << d)
    db = get_db()
    await db.create_weekly_schedule(post_id=post_id, hour=hour, minute=minute, days_mask=mask)
    await state.clear()
    await message.answer("Еженедельное расписание создано", reply_markup=admin_main_kb())


@router.message(ScheduleFSM.waiting_for_datetime)
async def schedule_datetime_entered(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    try:
        import pytz
        MOSCOW_TZ = pytz.timezone('Europe/Moscow')
        dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        dt = MOSCOW_TZ.localize(dt)
        ts = int(dt.timestamp())
    except Exception:
        await message.answer("Некорректный формат. Пример: 2025-12-31 23:59", reply_markup=back_kb())
        return
    await state.update_data(run_at=ts)
    await state.set_state(ScheduleFSM.waiting_for_repeat)
    await message.answer("Выберите повтор:", reply_markup=schedule_repeat_kb())


@router.callback_query(ScheduleFSM.waiting_for_repeat, F.data.startswith("repeat:"))
async def schedule_repeat_selected(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    repeat_raw = cb.data.split(":")[1]
    repeat_interval = None if repeat_raw == "none" else int(repeat_raw)

    data = await state.get_data()
    post_id = int(data["post_id"])
    run_at = int(data["run_at"])

    db = get_db()
    await db.create_schedule(post_id=post_id, next_run_at=run_at, repeat_interval=repeat_interval)

    await state.clear()
    await cb.message.edit_text("Расписание создано", reply_markup=admin_main_kb())
    await cb.answer()


@router.callback_query(F.data == "admin:list_schedules")
async def list_schedules(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    db = get_db()
    schedules = await db.list_schedules()
    weekly = await db.list_weekly_schedules()
    if not schedules and not weekly:
        await cb.message.edit_text("Нет расписаний", reply_markup=back_kb())
        await cb.answer()
        return
    lines = []
    for s in schedules[:20]:
        status = "⏸" if s["is_paused"] else ("🗑" if s["is_deleted"] else "▶️")
        lines.append(f"Один раз: {status} ID {s['id']} • пост {s['post_id']} • {to_moscow_time(s['next_run_at']).strftime('%Y-%m-%d %H:%M')} ({_format_repeat_interval(s['repeat_interval'])})")
    for ws in weekly[:20]:
        status = "⏸" if ws["is_paused"] else "▶️"
        days = _format_days_mask(ws["days_mask"])
        lines.append(f"Еженедельно: {status} ID {ws['id']} • пост {ws['post_id']} • {days} в {ws['hour']:02d}:{ws['minute']:02d}")
    await cb.message.edit_text("\n".join(lines), reply_markup=back_kb())
    await cb.answer()


@router.callback_query(F.data.in_({"admin:stats", "admin:stats_refresh"}))
async def admin_stats(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    
    try:
        db = get_db()

        # Определяем, это обновление или первое открытие
        is_refresh = cb.data == "admin:stats_refresh"
        
        # Получаем данные статистики с кэшированием
        total_users, today_users, data_updated = await get_stats_data(db, force_refresh=is_refresh)
        
        # Если это обновление и данные не изменились, показываем сообщение
        if is_refresh and not data_updated:
            await cb.answer("📊 Данные не изменились", show_alert=True)
            return

        # Получаем данные о постах (это быстрая операция)
        start_of_day = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        end_of_day = start_of_day + 24 * 3600
        todays_oneoff = await db.list_schedules_for_day(start_of_day, end_of_day) if hasattr(db, 'list_schedules_for_day') else []
        wday = datetime.now().weekday()
        todays_weekly = await db.list_weekly_for_day(wday)

        lines = [
            f"Юзеров сегодня: {today_users}",
            f"Всего юзеров: {total_users}",
            "",
            "Посты сегодня:",
        ]
        if not todays_oneoff and not todays_weekly:
            lines.append("— нет запланированных")
        else:
            for it in todays_oneoff:
                tm = to_moscow_time(it["next_run_at"]).strftime("%H:%M")
                title = it.get("title") or (it.get("text") or "").split("\n", 1)[0][:40]
                lines.append(f"{tm} — {title}")
            for it in todays_weekly:
                tm = f"{it['hour']:02d}:{it['minute']:02d}"
                title = it.get("title") or (it.get("text") or "").split("\n", 1)[0][:40]
                lines.append(f"{tm} — {title}")

        await cb.message.edit_text("\n".join(lines), reply_markup=stats_kb())
        
        # Показываем соответствующее сообщение
        if is_refresh:
            await cb.answer("📊 Статистика обновлена")
        else:
            await cb.answer()
            
    except Exception as e:
        # Если произошла ошибка, просто отвечаем на callback
        try:
            await cb.answer("📊 Статистика обновлена")
        except:
            pass  # Игнорируем ошибки с callback


@router.callback_query(F.data.startswith("post:delete:"))
async def delete_post(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    post_id = int(cb.data.split(":")[2])
    
    db = get_db()
    # Проверяем, есть ли активные расписания для этого поста
    schedules = await db.get_post_schedules(post_id)
    
    if schedules:
        # Показываем список расписаний с возможностью их удаления
        lines = [f"⚠️ Пост ID {post_id} имеет активные расписания:"]
        for s in schedules:
            if s["type"] == "oneoff":
                dt = to_moscow_time(s["next_run_at"]).strftime("%Y-%m-%d %H:%M")
                repeat = _format_repeat_interval(s["repeat_interval"])
                lines.append(f"📅 Один раз: {dt} ({repeat})")
            else:
                days = _format_days_mask(s["days_mask"])
                lines.append(f"🔄 Еженедельно: {days} в {s['hour']:02d}:{s['minute']:02d}")
        
        lines.append("\nСначала удалите все расписания, затем удалите пост.")
        
        # Создаем клавиатуру для удаления расписаний
        kb = InlineKeyboardBuilder()
        for s in schedules:
            if s["type"] == "oneoff":
                kb.button(text=f"🗑 Удалить расписание {s['id']}", callback_data=f"schedule:delete:{s['id']}:{post_id}")
            else:
                kb.button(text=f"🗑 Удалить еженедельное {s['id']}", callback_data=f"weekly:delete:{s['id']}:{post_id}")
                kb.button(text=f"✏️ Редактировать еженедельное {s['id']}", callback_data=f"weekly:edit:{s['id']}")
        kb.button(text="◀️ Назад", callback_data=f"admin:open_post:{post_id}:0")
        kb.adjust(1)
        
        await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    else:
        # Удаляем пост (каскадно удалятся все связанные записи)
        await db.delete_post(post_id)
        await cb.message.edit_text(
            f"✅ Пост ID {post_id} успешно удален",
            reply_markup=back_kb()
        )
    
    await cb.answer() 


@router.callback_query(F.data.startswith("schedule:delete:"))
async def delete_schedule(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    _, schedule_id, post_id = cb.data.split(":")[1:]
    schedule_id = int(schedule_id)
    post_id = int(post_id)
    
    db = get_db()
    await db.delete_schedule(schedule_id)
    
    # Проверяем, остались ли еще расписания
    remaining_schedules = await db.get_post_schedules(post_id)
    
    if remaining_schedules:
        # Показываем обновленный список
        lines = [f"✅ Расписание {schedule_id} удалено. Остались расписания:"]
        for s in remaining_schedules:
            if s["type"] == "oneoff":
                dt = to_moscow_time(s["next_run_at"]).strftime("%Y-%m-%d %H:%M")
                repeat = _format_repeat_interval(s["repeat_interval"])
                lines.append(f"📅 Один раз: {dt} ({repeat})")
            else:
                days = _format_days_mask(s["days_mask"])
                lines.append(f"🔄 Еженедельно: {days} в {s['hour']:02d}:{s['minute']:02d}")
        
        lines.append("\nУдалите все расписания, чтобы удалить пост.")
        
        kb = InlineKeyboardBuilder()
        for s in remaining_schedules:
            if s["type"] == "oneoff":
                kb.button(text=f"🗑 Удалить расписание {s['id']}", callback_data=f"schedule:delete:{s['id']}:{post_id}")
            else:
                kb.button(text=f"🗑 Удалить еженедельное {s['id']}", callback_data=f"weekly:delete:{s['id']}:{post_id}")
                kb.button(text=f"✏️ Редактировать еженедельное {s['id']}", callback_data=f"weekly:edit:{s['id']}")
        kb.button(text="◀️ Назад", callback_data=f"admin:open_post:{post_id}:0")
        kb.adjust(1)
        
        await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    else:
        # Все расписания удалены, можно удалить пост
        await cb.message.edit_text(
            f"✅ Все расписания для поста ID {post_id} удалены. Теперь можно удалить сам пост.",
            reply_markup=InlineKeyboardBuilder().button(
                text="🗑 Удалить пост", 
                callback_data=f"post:delete:{post_id}"
            ).button(
                text="◀️ Назад", 
                callback_data=f"admin:open_post:{post_id}:0"
            ).adjust(1).as_markup()
        )
    
    await cb.answer()


@router.callback_query(F.data.startswith("weekly:delete:"))
async def delete_weekly_schedule(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    _, schedule_id, post_id = cb.data.split(":")[1:]
    schedule_id = int(schedule_id)
    post_id = int(post_id)
    
    db = get_db()
    await db.delete_weekly_schedule(schedule_id)
    
    # Проверяем, остались ли еще расписания
    remaining_schedules = await db.get_post_schedules(post_id)
    
    if remaining_schedules:
        # Показываем обновленный список
        lines = [f"✅ Еженедельное расписание {schedule_id} удалено. Остались расписания:"]
        for s in remaining_schedules:
            if s["type"] == "oneoff":
                dt = to_moscow_time(s["next_run_at"]).strftime("%Y-%m-%d %H:%M")
                repeat = _format_repeat_interval(s["repeat_interval"])
                lines.append(f"📅 Один раз: {dt} ({repeat})")
            else:
                days = _format_days_mask(s["days_mask"])
                lines.append(f"🔄 Еженедельно: {days} в {s['hour']:02d}:{s['minute']:02d}")
        
        lines.append("\nУдалите все расписания, чтобы удалить пост.")
        
        kb = InlineKeyboardBuilder()
        for s in remaining_schedules:
            if s["type"] == "oneoff":
                kb.button(text=f"🗑 Удалить расписание {s['id']}", callback_data=f"schedule:delete:{s['id']}:{post_id}")
            else:
                kb.button(text=f"🗑 Удалить еженедельное {s['id']}", callback_data=f"weekly:delete:{s['id']}:{post_id}")
                kb.button(text=f"✏️ Редактировать еженедельное {s['id']}", callback_data=f"weekly:edit:{s['id']}")
        kb.button(text="◀️ Назад", callback_data=f"admin:open_post:{post_id}:0")
        kb.adjust(1)
        
        await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    else:
        # Все расписания удалены, можно удалить пост
        await cb.message.edit_text(
            f"✅ Все расписания для поста ID {post_id} удалены. Теперь можно удалить сам пост.",
            reply_markup=InlineKeyboardBuilder().button(
                text="🗑 Удалить пост", 
                callback_data=f"post:delete:{post_id}"
            ).button(
                text="◀️ Назад", 
                callback_data=f"admin:open_post:{post_id}:0"
            ).adjust(1).as_markup()
        )
    
    await cb.answer() 


@router.callback_query(F.data.startswith("weekly:edit:"))
async def edit_weekly_schedule_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    schedule_id = int(cb.data.split(":")[2])
    
    db = get_db()
    schedule = await db.get_weekly_schedule(schedule_id)
    
    if not schedule:
        await cb.answer("Расписание не найдено", show_alert=True)
        return
    
    # Сохраняем данные для редактирования
    await state.update_data(
        edit_schedule_id=schedule_id,
        edit_post_id=schedule["post_id"],
        weekly_days=set()
    )
    
    # Преобразуем маску дней в множество
    days = set()
    for i in range(7):
        if schedule["days_mask"] & (1 << i):
            days.add(i)
    
    await state.update_data(weekly_days=days)
    await state.set_state(ScheduleFSM.edit_weekly_select_days)
    
    await cb.message.edit_text(
        f"Редактирование еженедельного расписания {schedule_id}\n"
        f"Текущее время: {schedule['hour']:02d}:{schedule['minute']:02d}\n"
        f"Текущие дни: {_format_days_mask(schedule['days_mask'])}\n\n"
        "Выберите новые дни недели:",
        reply_markup=weekly_days_kb(selected_days=days)
    )
    await cb.answer() 


@router.callback_query(ScheduleFSM.edit_weekly_select_days, F.data.startswith("wday:"))
async def edit_weekly_select_days(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    _, _, value = cb.data.partition(":")
    if value == "done":
        data = await state.get_data()
        days: Set[int] = data.get("weekly_days") or set()
        if not days:
            await cb.answer("Выберите хотя бы один день", show_alert=True)
            return
        await state.set_state(ScheduleFSM.edit_weekly_time)
        await cb.message.edit_text("Укажите новое время в формате HH:MM:", reply_markup=back_kb())
        await cb.answer()
        return
    try:
        d = int(value)
        if d < 0 or d > 6:
            raise ValueError
    except Exception:
        await cb.answer("Неверный день", show_alert=True)
        return
    data = await state.get_data()
    days: Set[int] = data.get("weekly_days") or set()
    if d in days:
        days.remove(d)
    else:
        days.add(d)
    await state.update_data(weekly_days=days)
    
    # Обновляем клавиатуру с новыми галочками
    try:
        await cb.message.edit_reply_markup(reply_markup=weekly_days_kb(selected_days=days))
        # Небольшая задержка чтобы избежать flood control
        import asyncio
        await asyncio.sleep(0.2)
    except Exception as e:
        # Если flood control - просто пропускаем обновление клавиатуры
        if "Flood control" in str(e) or "Too Many Requests" in str(e):
            logger.debug("Skipping keyboard update due to flood control")
        else:
            logger.warning("Failed to update keyboard: %s", e)
    await cb.answer("OK") 


@router.message(ScheduleFSM.edit_weekly_time)
async def edit_weekly_time_enter(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    try:
        hh, mm = (message.text or "").strip().split(":", 1)
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except Exception:
        await message.answer("Неверное время. Пример: 12:30", reply_markup=back_kb())
        return
    
    data = await state.get_data()
    schedule_id = int(data["edit_schedule_id"])
    days: Set[int] = data.get("weekly_days") or set()
    
    # Создаем маску дней
    mask = 0
    for d in days:
        mask |= (1 << d)
    
    db = get_db()
    await db.update_weekly_schedule(schedule_id, hour, minute, mask)
    
    await state.clear()
    await message.answer(
        f"✅ Еженедельное расписание {schedule_id} обновлено\n"
        f"Новое время: {hour:02d}:{minute:02d}\n"
        f"Новые дни: {_format_days_mask(mask)}",
        reply_markup=admin_main_kb()
    )


# Обработчики для глобальной ссылки
@router.callback_query(F.data == "admin:global_link")
async def admin_global_link(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    
    db = get_db()
    current_link = await db.get_setting("global_link")
    current_button_text = await db.get_setting("global_button_text") or "🔗 Открыть сайт"
    
    text = f"Текущая глобальная ссылка: {current_link or 'не установлена'}\n"
    text += f"Текущий текст кнопки: {current_button_text}\n\n"
    text += "Выберите действие:"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="🔗 Изменить ссылку", callback_data="admin:change_global_link")
    kb.button(text="📝 Изменить текст кнопки", callback_data="admin:change_global_button_text")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(1, 1, 1)
    
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@router.callback_query(F.data == "admin:change_global_link")
async def change_global_link(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    
    await cb.message.edit_text(
        "Введите новую глобальную ссылку (или 'удалить' для удаления):",
        reply_markup=back_kb()
    )
    await state.set_state(GlobalLinkFSM.waiting_for_link)
    await cb.answer()


@router.message(GlobalLinkFSM.waiting_for_link)
async def receive_global_link(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    
    db = get_db()
    text = (message.text or "").strip()
    
    if text.lower() in {"удалить", "delete", "remove"}:
        await db.set_setting("global_link", None)
        await state.clear()
        await message.answer("✅ Глобальная ссылка удалена", reply_markup=admin_main_kb())
        return
    
    if not text.startswith(("http://", "https://")):
        await message.answer(
            "❌ Ссылка должна начинаться с http:// или https://\nПопробуйте еще раз:",
            reply_markup=back_kb()
        )
        return
    
    await db.set_setting("global_link", text)
    await state.clear()
    await message.answer(f"✅ Глобальная ссылка обновлена: {text}", reply_markup=admin_main_kb())


@router.callback_query(F.data == "admin:change_global_button_text")
async def change_global_button_text(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    
    await cb.message.edit_text(
        "Введите новый текст для кнопки:",
        reply_markup=back_kb()
    )
    await state.set_state(GlobalButtonTextFSM.waiting_for_text)
    await cb.answer()


@router.message(GlobalButtonTextFSM.waiting_for_text)
async def receive_global_button_text(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            "❌ Текст не может быть пустым. Попробуйте еще раз:",
            reply_markup=back_kb()
        )
        return
    
    db = get_db()
    await db.set_setting("global_button_text", text)
    await state.clear()
    await message.answer(f"✅ Текст кнопки обновлен: {text}", reply_markup=admin_main_kb()) 


@router.callback_query(F.data.startswith("post:publish:"))
async def publish_saved_post(cb: CallbackQuery) -> None:
    if not await _ensure_admin(cb):
        return
    post_id = int(cb.data.split(":")[2])
    db = get_db()
    post = await db.get_post(post_id)
    if not post:
        await cb.answer("Пост не найден", show_alert=True)
        return
    
    # Показываем начальное сообщение о начале рассылки
    progress_message = await cb.message.answer(
        "🚀 Начинаем рассылку...\n\n"
        "✅ Отправлено: 0 пользователям\n"
        "🚫 Заблокировали бота: 0 пользователей",
        reply_markup=admin_main_kb()
    )
    
    # Отправляем пост всем подписчикам
    stats = await send_post_to_all_subscribers(
        bot=cb.bot,
        db=db,
        content_type=post["content_type"],
        file_id=post.get("file_id"),
        text=post.get("text"),
        link_override=post.get("link_override"),
        button_text_override=post.get("button_text"),
        progress_message=progress_message,
    )
    
    # Обновляем сообщение с финальной статистикой
    final_message = (
        f"📊 Рассылка завершена!\n\n"
        f"✅ Отправлено: {stats['sent']} пользователям\n"
        f"🚫 Заблокировали бота: {stats['blocked']} пользователей\n\n"
        f"📢 Пост отправлен всем пользователям!"
    )
    
    await progress_message.edit_text(final_message, reply_markup=admin_main_kb(), parse_mode='Markdown')
    await cb.answer("Пост отправлен!", show_alert=True) 


@router.callback_query(F.data == "admin:fast_post")
async def fast_post_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.set_state(FastPostFSM.waiting_for_title)
    await cb.message.edit_text("Введите заголовок поста (для админ-списка):", reply_markup=back_kb())
    await cb.answer()

@router.message(FastPostFSM.waiting_for_title)
async def fast_post_title(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    await state.update_data(title=(message.text or "").strip() or None)
    await state.set_state(FastPostFSM.waiting_for_content)
    await message.answer("Отправьте контент поста: текст, фото, GIF или видео. Подпись используется как текст.", reply_markup=back_kb())

@router.message(FastPostFSM.waiting_for_content)
async def fast_post_content(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    content_type: Optional[str] = None
    file_id: Optional[str] = None
    text: Optional[str] = None
    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        text = message.caption or None
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
        text = message.caption or None
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
        text = message.caption or None
    elif message.text:
        content_type = "text"
        file_id = None
        text = message.text
    if not content_type:
        await message.answer("Не удалось распознать контент. Пришлите текст, фото, GIF или видео.", reply_markup=back_kb())
        return
    await state.update_data(content_type=content_type, file_id=file_id, text=text)
    await state.set_state(FastPostFSM.waiting_for_link)
    await message.answer("Укажите индивидуальную ссылку (или напишите 'пропустить'): ", reply_markup=back_kb())

@router.message(FastPostFSM.waiting_for_link)
async def fast_post_link(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    link_override = None
    if message.text and message.text.strip().lower() not in {"пропустить", "skip"}:
        link_override = message.text.strip()
    await state.update_data(link_override=link_override)
    await state.set_state(FastPostFSM.waiting_for_button_text)
    await message.answer("Текст кнопки (или 'пропустить' — будет глобальный/дефолтный):", reply_markup=back_kb())

@router.message(FastPostFSM.waiting_for_button_text)
async def fast_post_button_text(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    btn_text = None
    if message.text and message.text.strip().lower() not in {"пропустить", "skip"}:
        btn_text = message.text.strip()
    await state.update_data(button_text=btn_text)
    data = await state.get_data()
    db = get_db()
    stats = await send_post_to_all_subscribers(
        bot=message.bot,
        db=db,
        content_type=data["content_type"],
        file_id=data.get("file_id"),
        text=data.get("text"),
        link_override=data.get("link_override"),
        button_text_override=data.get("button_text"),
    )
    await state.clear()
    stats_message = (
        f"📊 Рассылка завершена!\n\n"
        f"✅ Отправлено: {stats['sent']} пользователям\n"
        f"🚫 Заблокировали бота: {stats['blocked']} пользователей"
    )
    await message.answer(stats_message, reply_markup=admin_main_kb()) 


@router.callback_query(F.data == "admin:schedule_post")
async def schedule_post_start(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.set_state(SchedulePostFSM.waiting_for_title)
    await cb.message.edit_text("Введите заголовок поста (для админ-списка):", reply_markup=back_kb())
    await cb.answer()

@router.message(SchedulePostFSM.waiting_for_title)
async def schedule_post_title(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    await state.update_data(title=(message.text or "").strip() or None)
    await state.set_state(SchedulePostFSM.waiting_for_content)
    await message.answer("Отправьте контент поста: текст, фото, GIF или видео. Подпись используется как текст.", reply_markup=back_kb())

@router.message(SchedulePostFSM.waiting_for_content)
async def schedule_post_content(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    content_type: Optional[str] = None
    file_id: Optional[str] = None
    text: Optional[str] = None
    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
        text = message.caption or None
    elif message.animation:
        content_type = "animation"
        file_id = message.animation.file_id
        text = message.caption or None
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
        text = message.caption or None
    elif message.text:
        content_type = "text"
        file_id = None
        text = message.text
    if not content_type:
        await message.answer("Не удалось распознать контент. Пришлите текст, фото, GIF или видео.", reply_markup=back_kb())
        return
    await state.update_data(content_type=content_type, file_id=file_id, text=text)
    await state.set_state(SchedulePostFSM.waiting_for_link)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="schedulepost:skip_link")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await message.answer("Укажите индивидуальную ссылку или нажмите 'Пропустить' для использования глобальной:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "schedulepost:skip_link")
async def schedule_skip_link(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.update_data(link_override=None)
    await state.set_state(SchedulePostFSM.waiting_for_button_text)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="schedulepost:skip_button")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await cb.message.edit_text("Укажите текст кнопки или нажмите 'Пропустить' для использования глобального/дефолтного:", reply_markup=kb.as_markup())
    await cb.answer("Ссылка пропущена")

@router.message(SchedulePostFSM.waiting_for_link)
async def schedule_post_link(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    link_override = message.text.strip() if message.text else None
    await state.update_data(link_override=link_override)
    await state.set_state(SchedulePostFSM.waiting_for_button_text)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="schedulepost:skip_button")
    kb.button(text="◀️ Отмена", callback_data="admin:back")
    kb.adjust(1, 1)
    await message.answer("Укажите текст кнопки или нажмите 'Пропустить' для использования глобального/дефолтного:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "schedulepost:skip_button")
async def schedule_skip_button(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    await state.update_data(button_text=None)
    await state.set_state(SchedulePostFSM.waiting_for_datetime)
    await cb.message.edit_text("Укажите дату и время публикации в формате YYYY-MM-DD HH:MM (по Москве):", reply_markup=back_kb())
    await cb.answer("Кнопка пропущена")

@router.message(SchedulePostFSM.waiting_for_button_text)
async def schedule_post_button_text(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    btn_text = message.text.strip() if message.text else None
    await state.update_data(button_text=btn_text)
    await state.set_state(SchedulePostFSM.waiting_for_datetime)
    await message.answer("Укажите дату и время публикации в формате YYYY-MM-DD HH:MM (по Москве):", reply_markup=back_kb())

@router.message(SchedulePostFSM.waiting_for_datetime)
async def schedule_post_datetime(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    try:
        import pytz
        MOSCOW_TZ = pytz.timezone('Europe/Moscow')
        dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
        dt = MOSCOW_TZ.localize(dt)
        ts = int(dt.timestamp())
    except Exception:
        await message.answer("Некорректный формат. Пример: 2025-12-31 23:59", reply_markup=back_kb())
        return
    await state.update_data(run_at=ts)
    await state.set_state(SchedulePostFSM.waiting_for_repeat)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="Без повтора", callback_data="schedulepost:repeat:none")
    kb.button(text="Еженедельно", callback_data="schedulepost:repeat:weekly")
    kb.adjust(1, 1)
    await message.answer("Выберите режим повтора:", reply_markup=kb.as_markup())

@router.callback_query(SchedulePostFSM.waiting_for_repeat, F.data.startswith("schedulepost:repeat:"))
async def schedule_post_repeat(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    repeat_raw = cb.data.split(":")[-1]
    data = await state.get_data()
    db = get_db()
    if repeat_raw == "none":
        # Разовая рассылка
        post_id = await db.create_post(
            title=data.get("title"),
            content_type=data["content_type"],
            file_id=data.get("file_id"),
            text=html_to_markdown(data.get("text")),
            link_override=data.get("link_override"),
            button_text=data.get("button_text"),
        )
        await db.create_schedule(post_id=post_id, next_run_at=data["run_at"], repeat_interval=None)
        await state.clear()
        await cb.message.answer("Пост запланирован!", reply_markup=admin_main_kb())
        await cb.answer("Пост добавлен в расписание!", show_alert=True)
        return
    elif repeat_raw == "weekly":
        # Переходим к выбору дней недели
        await state.update_data(weekly_days=set())
        await state.set_state(SchedulePostFSM.weekly_select_days)
        await cb.message.edit_text(
            "Выберите дни недели для рассылки (нажимайте, затем 'Готово'):",
            reply_markup=weekly_days_kb()
        )
        await cb.answer()

@router.message(SchedulePostFSM.weekly_select_days)
async def schedule_post_weekly_days(message: Message, state: FSMContext) -> None:
    # Игнорируем обычные сообщения, только кнопки
    await message.answer("Пожалуйста, выберите дни недели с помощью кнопок ниже.")

@router.callback_query(SchedulePostFSM.weekly_select_days, F.data.startswith("wday:"))
async def schedule_post_weekly_days_cb(cb: CallbackQuery, state: FSMContext) -> None:
    if not await _ensure_admin(cb):
        return
    _, _, value = cb.data.partition(":")
    if value == "done":
        data = await state.get_data()
        days: set = data.get("weekly_days") or set()
        if not days:
            await cb.answer("Выберите хотя бы один день", show_alert=True)
            return
        await state.set_state(SchedulePostFSM.waiting_for_weekly_time)
        await cb.message.edit_text("Укажите время рассылки в формате HH:MM:", reply_markup=back_kb())
        await cb.answer()
        return
    try:
        d = int(value)
        if d < 0 or d > 6:
            raise ValueError
    except Exception:
        await cb.answer("Неверный день", show_alert=True)
        return
    data = await state.get_data()
    days: set = data.get("weekly_days") or set()
    if d in days:
        days.remove(d)
    else:
        days.add(d)
    await state.update_data(weekly_days=days)
    try:
        await cb.message.edit_reply_markup(reply_markup=weekly_days_kb(selected_days=days))
        # Небольшая задержка чтобы избежать flood control
        import asyncio
        await asyncio.sleep(0.2)
    except Exception as e:
        # Если flood control - просто пропускаем обновление клавиатуры
        if "Flood control" in str(e) or "Too Many Requests" in str(e):
            logger.debug("Skipping keyboard update due to flood control")
        else:
            logger.warning("Failed to update keyboard: %s", e)
    await cb.answer("OK")

@router.message(SchedulePostFSM.waiting_for_weekly_time)
async def schedule_post_weekly_time(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message):
        return
    try:
        hh, mm = (message.text or "").strip().split(":", 1)
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except Exception:
        await message.answer("Неверное время. Пример: 12:30", reply_markup=back_kb())
        return
    data = await state.get_data()
    db = get_db()
    # Сохраняем пост
    post_id = await db.create_post(
        title=data.get("title"),
        content_type=data["content_type"],
        file_id=data.get("file_id"),
        text=html_to_markdown(data.get("text")),
        link_override=data.get("link_override"),
        button_text=data.get("button_text"),
    )
    days: set = data.get("weekly_days") or set()
    mask = 0
    for d in days:
        mask |= (1 << d)
    await db.create_weekly_schedule(post_id=post_id, hour=hour, minute=minute, days_mask=mask)
    await state.clear()
    await message.answer("Пост запланирован на выбранные дни недели!", reply_markup=admin_main_kb()) 