from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Set

from dotenv import load_dotenv


@dataclass
class Config:
    bot_token: str
    admin_ids: Set[int]
    database_path: str
    default_global_link: str | None
    default_destination_chat_id: int | None
    timezone: str


def load_config() -> Config:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is not set in environment")

    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    if not admin_ids_raw:
        raise RuntimeError("ADMIN_IDS is not set in environment")
    admin_ids: Set[int] = set()
    for part in admin_ids_raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            admin_ids.add(int(part))
        except ValueError:
            raise RuntimeError(f"Invalid admin id: {part}")

    database_path = os.getenv("DATABASE_PATH", "./bot.db").strip()

    default_global_link_env = os.getenv("DEFAULT_GLOBAL_LINK", "").strip()
    default_global_link = default_global_link_env or None

    default_dest_chat_raw = os.getenv("DEFAULT_DESTINATION_CHAT_ID", "").strip()
    default_destination_chat_id = int(default_dest_chat_raw) if default_dest_chat_raw else None

    timezone = os.getenv("TZ", "Europe/Moscow").strip()

    return Config(
        bot_token=bot_token,
        admin_ids=admin_ids,
        database_path=database_path,
        default_global_link=default_global_link,
        default_destination_chat_id=default_destination_chat_id,
        timezone=timezone,
    ) 