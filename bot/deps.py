from __future__ import annotations

from typing import Optional

from bot.config import Config
from bot.db import Database

_config: Optional[Config] = None
_db: Optional[Database] = None


def set_config(config: Config) -> None:
    global _config
    _config = config


def set_db(db: Database) -> None:
    global _db
    _db = db


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("Config is not initialized")
    return _config


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database is not initialized")
    return _db 