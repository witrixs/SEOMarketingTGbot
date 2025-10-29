from __future__ import annotations

from pathlib import Path

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile, ChatMemberUpdated, ChatJoinRequest
from aiogram.enums import ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.posts import send_post_to_chat
from bot.deps import get_db, get_config

router = Router(name="public")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db = get_db()
    user = message.from_user
    if user:
        await db.add_or_update_subscriber(user.id, user.first_name, user.username)

    # Проверяем, есть ли параметр start для трекинга
    start_param = None
    if message.text and " " in message.text:
        start_param = message.text.split(" ", 1)[1].strip()
    
    # Если есть параметр трекинга, обрабатываем его
    if start_param:
        tracking_link = await db.get_tracking_link(start_param)
        if tracking_link:
            # Увеличиваем счетчик кликов
            await db.increment_tracking_clicks(start_param)
            
            # Отслеживаем взаимодействие пользователя
            if user:
                is_new_user = await db.track_user_interaction(start_param, user.id)
                
                # Можем добавить логирование для отладки
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"User {user.id} started via tracking link {start_param} (new: {is_new_user})")

    default_text = (
        "🌟 Ваш гид по бонусам! 🌟\n\n"
        "Узнайте, как получить:\n"
        "🎁 Бонус 500% на первые депозиты\n"
        "💸 30% кэшбэк каждую неделю\n"
        "⚡️ Быстрые выплаты на карту или крипту\n"
        "❤️ Поддержка 24/7\n\n"
        "Оставайтесь с нами, чтобы быть первыми! 🚀 Внесите депозит"
    )

    media_path = Path.cwd() / "media" / "Global-post.jpg"
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


@router.my_chat_member()
async def handle_chat_member_update(update: ChatMemberUpdated) -> None:
    """Обработчик для принятия заявок в группы с автопринятием"""
    import logging
    logger = logging.getLogger(__name__)
    
    db = get_db()
    
    # Проверяем, что пользователь был принят в группу
    if (update.old_chat_member.status == ChatMemberStatus.LEFT and 
        update.new_chat_member.status == ChatMemberStatus.MEMBER):
        
        # Проверяем, есть ли эта группа в списке автопринятия
        group = await db.get_auto_approve_group(update.chat.id)
        if group and group["enabled"]:
            # Получаем глобальную ссылку из настроек
            global_link = await db.get_setting("global_link")
            if not global_link:
                global_link = "https://cutt.ly/fwMdyh5K"  # fallback
            
            # Отправляем приветственное сообщение пользователю
            welcome_text = (
                "🎉 Бэм, еще плюсовая)))\n"
                "✅ Вы приняты в группу!!!\n\n"
                "🎁 БОНУС +200% на первый депозит"
            )
            
            # Создаем клавиатуру с кнопкой
            kb = InlineKeyboardBuilder()
            kb.button(text="🎁 Забрать бонус", url=global_link)
            kb.adjust(1)
            
            try:
                # Отправляем видео с текстом и кнопкой
                await update.bot.send_video(
                    chat_id=update.from_user.id,
                    video="https://hooks.pro/media/2025/05/22/bot7744916291/file-aC9tTQhBdX.MP4",
                    caption=welcome_text,
                    reply_markup=kb.as_markup()
                )
            except Exception as e:
                # Если не удалось отправить сообщение (пользователь заблокировал бота и т.д.)
                logger.error(f"Не удалось отправить приветственное сообщение пользователю {update.from_user.id}: {e}")


@router.chat_join_request()
async def handle_chat_join_request(update, bot) -> None:
    """Обработчик для принятия заявок на вступление в группы"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        db = get_db()
        # В этом обработчике update уже является ChatJoinRequest
        chat_id = update.chat.id
        user_id = update.from_user.id
        
        logger.info(f"Получена заявка: пользователь {user_id} в группу {chat_id}")
        
        # Проверяем, есть ли эта группа в списке автопринятия
        group = await db.get_auto_approve_group(chat_id)
        
        if group and group["enabled"]:
            try:
                # Принимаем заявку
                await bot.approve_chat_join_request(
                    chat_id=chat_id,
                    user_id=user_id
                )
                logger.info(f"Заявка принята: пользователь {user_id} в группу {chat_id}")
                
                # Получаем глобальную ссылку из настроек
                global_link = await db.get_setting("global_link")
                if not global_link:
                    global_link = "https://cutt.ly/fwMdyh5K"  # fallback
                
                # Отправляем приветственное сообщение пользователю
                welcome_text = (
                    "🎉 Бэм, еще плюсовая)))\n"
                    "✅ Вы приняты в группу!!!\n\n"
                    "🎁 БОНУС +200% на первый депозит"
                )
                
                # Создаем клавиатуру с кнопкой
                kb = InlineKeyboardBuilder()
                kb.button(text="🎁 Забрать бонус", url=global_link)
                kb.adjust(1)
                
                try:
                    # Отправляем видео с текстом и кнопкой
                    await bot.send_video(
                        chat_id=user_id,
                        video="https://hooks.pro/media/2025/05/22/bot7744916291/file-aC9tTQhBdX.MP4",
                        caption=welcome_text,
                        reply_markup=kb.as_markup()
                    )
                    logger.info(f"Приветственное сообщение отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"Не удалось отправить приветственное сообщение пользователю {user_id}: {e}")
                    
            except Exception as e:
                logger.error(f"Не удалось принять заявку пользователя {user_id} в группу {chat_id}: {e}")
        else:
            logger.info(f"Группа {chat_id} не настроена для автопринятия или отключена")
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике заявок: {e}")


# Вспомогательная функция для проверки админов
async def _is_admin(message: Message) -> bool:
    """Проверяет, является ли пользователь админом"""
    config = get_config()
    return message.from_user and message.from_user.id in config.admin_ids


# Обработчик всех сообщений (кроме команд) для обычных пользователей
@router.message()
async def handle_any_message(message: Message) -> None:
    """Отвечает выбранным постом на любое сообщение от не-админа"""
    
    # Проверяем, что это не админ
    if await _is_admin(message):
        return
    
    # Проверяем, что это личный чат (не группа)
    if message.chat.type != "private":
        return
    
    db = get_db()
    user = message.from_user
    if user:
        await db.add_or_update_subscriber(user.id, user.first_name, user.username)

    # Проверяем, установлен ли автоответ
    auto_reply_post_id = await db.get_setting("auto_reply_post_id")
    
    if auto_reply_post_id and auto_reply_post_id.strip():
        # Если установлен автоответ - отправляем выбранный пост
        try:
            post_id = int(auto_reply_post_id)
            post = await db.get_post(post_id)
            
            if post:
                await send_post_to_chat(
                    bot=message.bot,
                    db=db,
                    chat_id=message.chat.id,
                    content_type=post["content_type"],
                    file_id=post.get("file_id"),
                    text=post.get("text"),
                    link_override=post.get("link_override"),
                    button_text_override=post.get("button_text"),
                )
                return
        except (ValueError, TypeError):
            # Если ID поста некорректный, продолжаем с дефолтным поведением
            pass
    
    # Если автоответ не установлен или пост не найден - отправляем стартовое сообщение
    default_text = (
        "Стрим с тамаевым будет тут  - https://t.me/+vFXyIipSHpczOGVi\n\n"
        "Промокод QMELL\n\n"
        "500% бонус за первый депозит - Забирай тут 👇"
    )

    media_path = Path.cwd() / "media" / "Global-post.jpg"
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

