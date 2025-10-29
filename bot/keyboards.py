from __future__ import annotations

from typing import Optional, List, Dict, Set

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_main_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Новый пост", callback_data="admin:create_post")
    kb.button(text="🗓 Запланировать пост", callback_data="admin:schedule_post")
    kb.button(text="🗂 Посты", callback_data="admin:list_posts")
    kb.button(text="🕒 Расписания", callback_data="admin:list_schedules")
    kb.button(text="🤖 Автоответ", callback_data="admin:auto_reply")
    kb.button(text="🌐 Глобальная ссылка", callback_data="admin:global_link")
    kb.button(text="🔗 Трекинг ссылок", callback_data="admin:tracking_links")
    kb.button(text="👥 Группы автопринятия", callback_data="admin:auto_approve_groups")
    kb.button(text="📊 Статистика", callback_data="admin:stats")
    kb.adjust(2, 2, 1, 2, 1, 1)
    return kb.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data="admin:back")
    return kb.as_markup()


def stats_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="admin:stats_refresh")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(1, 1)
    return kb.as_markup()


def post_actions_kb(post_id: int, back_page: Optional[int] = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👁️ Предпросмотр", callback_data=f"post:preview:{post_id}")
    kb.button(text="🚀 Опубликовать", callback_data=f"post:publish:{post_id}")
    kb.button(text="🕒 Запланировать", callback_data=f"post:schedule:{post_id}")
    kb.button(text="🤖 Установить автоответ", callback_data=f"post:set_auto_reply:{post_id}")
    kb.button(text="🗑 Удалить", callback_data=f"post:delete:{post_id}")
    if back_page is not None:
        kb.button(text="◀️ Назад к списку", callback_data=f"post:back_to_list:{back_page}")
        kb.adjust(2, 2, 1, 1)
    else:
        kb.adjust(2, 2, 1)
    return kb.as_markup()


def schedule_repeat_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Без повтора", callback_data="repeat:none")
    kb.button(text="Каждый 1ч", callback_data="repeat:3600")
    kb.button(text="Каждые 3ч", callback_data="repeat:10800")
    kb.button(text="Каждые 6ч", callback_data="repeat:21600")
    kb.button(text="Каждые 12ч", callback_data="repeat:43200")
    kb.button(text="Каждые 24ч", callback_data="repeat:86400")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def post_link_kb(url: Optional[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if url:
        kb.button(text="🔗 Открыть сайт", url=url)
    return kb.as_markup()


def schedule_mode_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Еженедельно по дням", callback_data="sched:mode:weekly")
    kb.button(text="Один раз/по интервалу", callback_data="sched:mode:oneoff")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def weekly_days_kb(selected_days: Optional[Set[int]] = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    selected_days = selected_days or set()
    
    # Создаем кнопки с галочками для выбранных дней
    for i, day_name in enumerate(["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]):
        if i in selected_days:
            kb.button(text=f"✅ {day_name}", callback_data=f"wday:{i}")
        else:
            kb.button(text=f"⬜ {day_name}", callback_data=f"wday:{i}")
    
    kb.button(text="✅ Готово", callback_data="wday:done")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(7, 2)
    return kb.as_markup()


def posts_page_kb(posts: List[Dict], page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in posts:
        kb.button(text=f"Открыть ID {p['id']}", callback_data=f"admin:open_post:{p['id']}:{page}")
    if has_prev:
        kb.button(text="⬅️ Назад", callback_data=f"admin:list_posts:page:{page-1}")
    if has_next:
        kb.button(text="Вперёд ➡️", callback_data=f"admin:list_posts:page:{page+1}")
    kb.button(text="◀️ Меню", callback_data="admin:back")
    kb.adjust(2)
    return kb.as_markup()


def auto_approve_groups_kb(groups: List[Dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить группу", callback_data="admin:add_auto_approve_group")
    kb.button(text="◀️ Назад", callback_data="admin:back")
    kb.adjust(1, 1)
    
    if groups:
        for group in groups:
            status = "✅" if group["enabled"] else "❌"
            title = group["title"] or f"ID {group['chat_id']}"
            kb.button(
                text=f"{status} {title}",
                callback_data=f"admin:auto_approve_group:{group['chat_id']}"
            )
        kb.adjust(1)
    
    return kb.as_markup()


def auto_approve_group_actions_kb(chat_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Включить/Выключить", callback_data=f"admin:toggle_auto_approve_group:{chat_id}")
    kb.button(text="🗑 Удалить группу", callback_data=f"admin:remove_auto_approve_group:{chat_id}")
    kb.button(text="◀️ Назад", callback_data="admin:auto_approve_groups")
    kb.adjust(1, 1, 1)
    return kb.as_markup()


# Tracking links keyboards
def tracking_links_main_kb() -> InlineKeyboardMarkup:
    """Главное меню трекинга ссылок"""
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать ссылку", callback_data="tracking:create")
    kb.button(text="📋 Мои ссылки", callback_data="tracking:list")
    kb.button(text="📊 Общая статистика", callback_data="tracking:stats")
    kb.button(text="🔙 Назад", callback_data="admin:back")
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def tracking_link_actions_kb(tracking_id: str) -> InlineKeyboardMarkup:
    """Действия с конкретной трекинговой ссылкой"""
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data=f"tracking:stats:{tracking_id}")
    kb.button(text="❌ Удалить", callback_data=f"tracking:delete:{tracking_id}")
    kb.button(text="🔄 Обновить", callback_data=f"tracking:refresh:{tracking_id}")
    kb.button(text="⬅ Назад", callback_data="tracking:list")
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def tracking_confirm_delete_kb(tracking_id: str) -> InlineKeyboardMarkup:
    """Подтверждение удаления трекинговой ссылки"""
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"tracking:delete_confirm:{tracking_id}")
    kb.button(text="❌ Нет", callback_data=f"tracking:stats:{tracking_id}")
    kb.adjust(2)
    return kb.as_markup()


def tracking_stats_kb() -> InlineKeyboardMarkup:
    """Клавиатура для общей статистики"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="tracking:stats_refresh")
    kb.button(text="⬅ Назад", callback_data="admin:tracking_links")
    kb.adjust(1, 1)
    return kb.as_markup() 