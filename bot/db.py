from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not initialized")
        return self._conn

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self.connection.execute("PRAGMA journal_mode=WAL;")
        await self.connection.execute("PRAGMA foreign_keys=ON;")

        await self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,                         -- optional title for admin listing
                content_type TEXT NOT NULL,          -- text | photo | animation | video
                file_id TEXT,                        -- nullable for text
                text TEXT,                           -- caption or text
                link_override TEXT,                  -- optional per-post URL
                button_text TEXT,                    -- optional per-post button label
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_stats (
                post_id INTEGER PRIMARY KEY,
                delivered_count INTEGER NOT NULL DEFAULT 0,
                last_delivered_at INTEGER,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            -- Subscribers are users who pressed /start
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                joined_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS destinations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL UNIQUE,
                title TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                next_run_at INTEGER NOT NULL,
                repeat_interval INTEGER,        -- seconds, nullable
                is_paused INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                last_run_at INTEGER,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            -- Weekly recurring schedules independent of date
            CREATE TABLE IF NOT EXISTS weekly_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                days_mask INTEGER NOT NULL,       -- bitmask Mon=0..Sun=6
                is_paused INTEGER NOT NULL DEFAULT 0,
                last_run_ymd INTEGER,             -- YYYYMMDD to prevent double-send per day
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS auto_cycle (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                interval_seconds INTEGER DEFAULT 0,
                last_post_id INTEGER
            );
            INSERT OR IGNORE INTO auto_cycle (id, enabled, interval_seconds, last_post_id) VALUES (1, 0, 0, NULL);
            """
        )
        await self.connection.commit()

        # Lightweight migrations
        async with self.connection.execute("PRAGMA table_info(posts)") as cur:
            cols = await cur.fetchall()
            col_names = {c[1] for c in cols}
        if "button_text" not in col_names:
            await self.connection.execute("ALTER TABLE posts ADD COLUMN button_text TEXT")
            await self.connection.commit()
        if "title" not in col_names:
            await self.connection.execute("ALTER TABLE posts ADD COLUMN title TEXT")
            await self.connection.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # Settings
    async def get_setting(self, key: str) -> Optional[str]:
        async with self.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.connection.commit()

    # Posts
    async def create_post(
        self,
        *,
        title: Optional[str],
        content_type: str,
        file_id: Optional[str],
        text: Optional[str],
        link_override: Optional[str],
        button_text: Optional[str],
    ) -> int:
        now = int(time.time())
        async with self.connection.execute(
            "INSERT INTO posts(title, content_type, file_id, text, link_override, button_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, content_type, file_id, text, link_override, button_text, now),
        ) as cursor:
            await self.connection.commit()
            post_id = cursor.lastrowid
        await self.connection.execute("INSERT INTO post_stats(post_id, delivered_count) VALUES (?, 0)", (post_id,))
        await self.connection.commit()
        return post_id

    async def update_post(self, post_id: int, *, title: Optional[str] = None, text: Optional[str] = None, link_override: Optional[str] = None, button_text: Optional[str] = None) -> None:
        fields = []
        values: List[Any] = []
        if title is not None:
            fields.append("title = ?")
            values.append(title)
        if text is not None:
            fields.append("text = ?")
            values.append(text)
        if link_override is not None:
            fields.append("link_override = ?")
            values.append(link_override)
        if button_text is not None:
            fields.append("button_text = ?")
            values.append(button_text)
        if not fields:
            return
        values.append(post_id)
        await self.connection.execute(f"UPDATE posts SET {', '.join(fields)} WHERE id = ?", values)
        await self.connection.commit()

    async def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT id, title, content_type, file_id, text, link_override, button_text, created_at FROM posts WHERE id = ?",
            (post_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "title": row[1],
                "content_type": row[2],
                "file_id": row[3],
                "text": row[4],
                "link_override": row[5],
                "button_text": row[6],
                "created_at": row[7],
            }

    async def list_posts(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT id, title, content_type, file_id, text, link_override, button_text, created_at FROM posts ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "title": row[1],
                    "content_type": row[2],
                    "file_id": row[3],
                    "text": row[4],
                    "link_override": row[5],
                    "button_text": row[6],
                    "created_at": row[7],
                }
                for row in rows
            ]

    async def delete_post(self, post_id: int) -> None:
        await self.connection.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        await self.connection.commit()

    async def increment_post_delivery(self, post_id: int, delivered: int) -> None:
        now = int(time.time())
        await self.connection.execute(
            "UPDATE post_stats SET delivered_count = delivered_count + ?, last_delivered_at = ? WHERE post_id = ?",
            (delivered, now, post_id),
        )
        await self.connection.commit()

    async def get_post_stats(self, post_id: int) -> Optional[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT delivered_count, last_delivered_at FROM post_stats WHERE post_id = ?",
            (post_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {"delivered_count": row[0], "last_delivered_at": row[1]}

    # Subscribers
    async def add_or_update_subscriber(self, user_id: int, first_name: Optional[str], username: Optional[str]) -> None:
        now = int(time.time())
        await self.connection.execute(
            """
            INSERT INTO subscribers(user_id, first_name, username, is_active, joined_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                username = excluded.username,
                is_active = 1
            """,
            (user_id, first_name, username, now),
        )
        await self.connection.commit()

    async def set_subscriber_active(self, user_id: int, active: bool) -> None:
        await self.connection.execute(
            "UPDATE subscribers SET is_active = ? WHERE user_id = ?",
            (1 if active else 0, user_id),
        )
        await self.connection.commit()

    async def list_active_subscribers(self, limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT user_id, first_name, username FROM subscribers WHERE is_active = 1 ORDER BY joined_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"user_id": row[0], "first_name": row[1], "username": row[2]}
                for row in rows
            ]

    async def count_active_subscribers(self) -> int:
        async with self.connection.execute(
            "SELECT COUNT(*) FROM subscribers WHERE is_active = 1"
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def count_all_subscribers(self) -> int:
        async with self.connection.execute(
            "SELECT COUNT(*) FROM subscribers"
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def count_new_subscribers_since(self, since_ts: int) -> int:
        async with self.connection.execute(
            "SELECT COUNT(*) FROM subscribers WHERE is_active = 1 AND joined_at >= ?",
            (since_ts,),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    # One-off schedules
    async def create_schedule(self, post_id: int, next_run_at: int, repeat_interval: Optional[int]) -> int:
        async with self.connection.execute(
            "INSERT INTO schedules(post_id, next_run_at, repeat_interval, is_paused, is_deleted) VALUES (?, ?, ?, 0, 0)",
            (post_id, next_run_at, repeat_interval),
        ) as cursor:
            await self.connection.commit()
            return cursor.lastrowid

    async def list_schedules(self) -> List[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT id, post_id, next_run_at, repeat_interval, is_paused, is_deleted, last_run_at FROM schedules ORDER BY next_run_at ASC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "post_id": row[1],
                    "next_run_at": row[2],
                    "repeat_interval": row[3],
                    "is_paused": bool(row[4]),
                    "is_deleted": bool(row[5]),
                    "last_run_at": row[6],
                }
                for row in rows
            ]

    async def list_due_schedules(self, now_ts: int) -> List[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT id, post_id, next_run_at, repeat_interval FROM schedules WHERE is_deleted = 0 AND is_paused = 0 AND next_run_at <= ?",
            (now_ts,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"id": row[0], "post_id": row[1], "next_run_at": row[2], "repeat_interval": row[3]}
                for row in rows
            ]

    async def mark_schedule_after_run(self, schedule_id: int, repeat_interval: Optional[int]) -> None:
        now = int(time.time())
        if repeat_interval and repeat_interval > 0:
            await self.connection.execute(
                "UPDATE schedules SET last_run_at = ?, next_run_at = next_run_at + ? WHERE id = ?",
                (now, repeat_interval, schedule_id),
            )
        else:
            await self.connection.execute(
                "UPDATE schedules SET last_run_at = ?, is_deleted = 1 WHERE id = ?",
                (now, schedule_id),
            )
        await self.connection.commit()

    # Weekly schedules
    async def create_weekly_schedule(self, post_id: int, hour: int, minute: int, days_mask: int) -> int:
        async with self.connection.execute(
            "INSERT INTO weekly_schedules(post_id, hour, minute, days_mask, is_paused, last_run_ymd) VALUES (?, ?, ?, ?, 0, NULL)",
            (post_id, hour, minute, days_mask),
        ) as cursor:
            await self.connection.commit()
            return cursor.lastrowid

    async def list_weekly_schedules(self) -> List[Dict[str, Any]]:
        async with self.connection.execute(
            "SELECT id, post_id, hour, minute, days_mask, is_paused, last_run_ymd FROM weekly_schedules ORDER BY hour, minute"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "post_id": row[1],
                    "hour": row[2],
                    "minute": row[3],
                    "days_mask": row[4],
                    "is_paused": bool(row[5]),
                    "last_run_ymd": row[6],
                }
                for row in rows
            ]

    async def list_weekly_due(self, wday: int, hour: int, minute: int, today_ymd: int) -> List[Dict[str, Any]]:
        bit = 1 << wday
        query = (
            "SELECT id, post_id FROM weekly_schedules "
            "WHERE is_paused = 0 AND hour = ? AND minute = ? AND (days_mask & ?) != 0 "
            "AND (last_run_ymd IS NULL OR last_run_ymd <> ?)"
        )
        async with self.connection.execute(query, (hour, minute, bit, today_ymd)) as cursor:
            rows = await cursor.fetchall()
            return [{"id": row[0], "post_id": row[1]} for row in rows]

    async def mark_weekly_ran(self, schedule_id: int, today_ymd: int) -> None:
        await self.connection.execute(
            "UPDATE weekly_schedules SET last_run_ymd = ? WHERE id = ?",
            (today_ymd, schedule_id),
        )
        await self.connection.commit()

    async def list_weekly_for_day(self, wday: int) -> List[Dict[str, Any]]:
        bit = 1 << wday
        query = (
            "SELECT ws.id, ws.post_id, ws.hour, ws.minute, p.title, p.text FROM weekly_schedules ws "
            "JOIN posts p ON p.id = ws.post_id WHERE (ws.days_mask & ?) != 0 AND ws.is_paused = 0 "
            "ORDER BY ws.hour, ws.minute"
        )
        async with self.connection.execute(query, (bit,)) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "post_id": row[1],
                    "hour": row[2],
                    "minute": row[3],
                    "title": row[4],
                    "text": row[5],
                }
                for row in rows
            ]

    async def set_schedule_paused(self, schedule_id: int, paused: bool) -> None:
        await self.connection.execute(
            "UPDATE schedules SET is_paused = ? WHERE id = ?",
            (1 if paused else 0, schedule_id),
        )
        await self.connection.commit()

    async def delete_schedule(self, schedule_id: int) -> None:
        await self.connection.execute("UPDATE schedules SET is_deleted = 1 WHERE id = ?", (schedule_id,))
        await self.connection.commit()

    async def delete_weekly_schedule(self, schedule_id: int) -> None:
        await self.connection.execute("DELETE FROM weekly_schedules WHERE id = ?", (schedule_id,))
        await self.connection.commit()

    async def get_post_schedules(self, post_id: int) -> List[Dict[str, Any]]:
        """Получить все расписания для поста"""
        schedules = []
        
        # Обычные расписания
        async with self.connection.execute(
            "SELECT id, next_run_at, repeat_interval, is_paused, is_deleted FROM schedules WHERE post_id = ? AND is_deleted = 0",
            (post_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                schedules.append({
                    "id": row[0],
                    "type": "oneoff",
                    "next_run_at": row[1],
                    "repeat_interval": row[2],
                    "is_paused": bool(row[3]),
                    "is_deleted": bool(row[4])
                })
        
        # Еженедельные расписания
        async with self.connection.execute(
            "SELECT id, hour, minute, days_mask, is_paused FROM weekly_schedules WHERE post_id = ? AND is_paused = 0",
            (post_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                schedules.append({
                    "id": row[0],
                    "type": "weekly",
                    "hour": row[1],
                    "minute": row[2],
                    "days_mask": row[3],
                    "is_paused": bool(row[4])
                })
        
        return schedules

    async def get_weekly_schedule(self, schedule_id: int) -> Optional[Dict[str, Any]]:
        """Получить еженедельное расписание по ID"""
        async with self.connection.execute(
            "SELECT id, post_id, hour, minute, days_mask, is_paused FROM weekly_schedules WHERE id = ?",
            (schedule_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "post_id": row[1],
                "hour": row[2],
                "minute": row[3],
                "days_mask": row[4],
                "is_paused": bool(row[5])
            }

    async def update_weekly_schedule(self, schedule_id: int, hour: int, minute: int, days_mask: int) -> None:
        """Обновить еженедельное расписание"""
        await self.connection.execute(
            "UPDATE weekly_schedules SET hour = ?, minute = ?, days_mask = ? WHERE id = ?",
            (hour, minute, days_mask, schedule_id)
        )
        await self.connection.commit()

    # Auto-cycle
    async def get_auto_cycle(self) -> Dict[str, Any]:
        async with self.connection.execute(
            "SELECT enabled, interval_seconds, last_post_id FROM auto_cycle WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            return {
                "enabled": bool(row[0]),
                "interval_seconds": row[1],
                "last_post_id": row[2],
            }

    async def set_auto_cycle(self, enabled: bool, interval_seconds: int) -> None:
        await self.connection.execute(
            "UPDATE auto_cycle SET enabled = ?, interval_seconds = ? WHERE id = 1",
            (1 if enabled else 0, interval_seconds),
        )
        await self.connection.commit()

    async def set_auto_cycle_last_post(self, last_post_id: Optional[int]) -> None:
        await self.connection.execute(
            "UPDATE auto_cycle SET last_post_id = ? WHERE id = 1",
            (last_post_id,),
        )
        await self.connection.commit() 