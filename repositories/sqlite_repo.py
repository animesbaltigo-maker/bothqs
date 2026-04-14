from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from config import DATA_DIR

DB_PATH = DATA_DIR / "hq_library.sqlite3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA busy_timeout=30000;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_library_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id TEXT NOT NULL,
                hq_id TEXT NOT NULL,
                title TEXT NOT NULL,
                publisher_name TEXT,
                status TEXT,
                cover_url TEXT,
                site_url TEXT,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, hq_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reading_progress (
                user_id TEXT NOT NULL,
                hq_id TEXT NOT NULL,
                title TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_number TEXT NOT NULL,
                page_number INTEGER NOT NULL DEFAULT 1,
                page_count INTEGER NOT NULL DEFAULT 1,
                reader_url TEXT,
                cover_url TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, hq_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                hq_id TEXT NOT NULL,
                title TEXT NOT NULL,
                chapter_id TEXT,
                chapter_number TEXT,
                page_number INTEGER,
                cover_url TEXT,
                site_url TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_favorites_user
            ON favorites(user_id, added_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_progress_user
            ON reading_progress(user_id, updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_user
            ON history(user_id, created_at DESC)
            """
        )


def add_favorite(user_id: int | str, item: dict[str, Any]) -> None:
    now = _utc_now()
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO favorites (
                user_id,
                hq_id,
                title,
                publisher_name,
                status,
                cover_url,
                site_url,
                added_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, hq_id) DO UPDATE SET
                title = excluded.title,
                publisher_name = excluded.publisher_name,
                status = excluded.status,
                cover_url = excluded.cover_url,
                site_url = excluded.site_url
            """,
            (
                str(user_id),
                str(item.get("hq_id") or ""),
                str(item.get("title") or "HQ"),
                str(item.get("publisher_name") or ""),
                str(item.get("status") or ""),
                str(item.get("cover_url") or ""),
                str(item.get("site_url") or ""),
                now,
            ),
        )


def remove_favorite(user_id: int | str, hq_id: str | int) -> None:
    with _get_conn() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND hq_id = ?",
            (str(user_id), str(hq_id)),
        )


def is_favorite(user_id: int | str, hq_id: str | int) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND hq_id = ? LIMIT 1",
            (str(user_id), str(hq_id)),
        ).fetchone()
    return row is not None


def count_favorites(user_id: int | str) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM favorites WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
    return int(row["total"] if row else 0)


def list_favorites(user_id: int | str, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT hq_id, title, publisher_name, status, cover_url, site_url, added_at
            FROM favorites
            WHERE user_id = ?
            ORDER BY added_at DESC, title ASC
            LIMIT ? OFFSET ?
            """,
            (str(user_id), max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    return [dict(row) for row in rows]


def save_progress(
    user_id: int | str,
    *,
    hq_id: str | int,
    title: str,
    chapter_id: str | int,
    chapter_number: str | int,
    page_number: int,
    page_count: int,
    reader_url: str,
    cover_url: str = "",
) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO reading_progress (
                user_id,
                hq_id,
                title,
                chapter_id,
                chapter_number,
                page_number,
                page_count,
                reader_url,
                cover_url,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, hq_id) DO UPDATE SET
                title = excluded.title,
                chapter_id = excluded.chapter_id,
                chapter_number = excluded.chapter_number,
                page_number = excluded.page_number,
                page_count = excluded.page_count,
                reader_url = excluded.reader_url,
                cover_url = excluded.cover_url,
                updated_at = excluded.updated_at
            """,
            (
                str(user_id),
                str(hq_id),
                str(title or "HQ"),
                str(chapter_id),
                str(chapter_number),
                max(1, int(page_number)),
                max(1, int(page_count)),
                str(reader_url or ""),
                str(cover_url or ""),
                _utc_now(),
            ),
        )


def get_progress(user_id: int | str, hq_id: str | int) -> dict[str, Any] | None:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT user_id, hq_id, title, chapter_id, chapter_number, page_number, page_count,
                   reader_url, cover_url, updated_at
            FROM reading_progress
            WHERE user_id = ? AND hq_id = ?
            LIMIT 1
            """,
            (str(user_id), str(hq_id)),
        ).fetchone()
    return dict(row) if row else None


def get_last_progress(user_id: int | str) -> dict[str, Any] | None:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT user_id, hq_id, title, chapter_id, chapter_number, page_number, page_count,
                   reader_url, cover_url, updated_at
            FROM reading_progress
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (str(user_id),),
        ).fetchone()
    return dict(row) if row else None


def list_recent_progress(user_id: int | str, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT user_id, hq_id, title, chapter_id, chapter_number, page_number, page_count,
                   reader_url, cover_url, updated_at
            FROM reading_progress
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (str(user_id), max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    return [dict(row) for row in rows]


def add_history(
    user_id: int | str,
    *,
    event_type: str,
    hq_id: str | int,
    title: str,
    chapter_id: str | int | None = None,
    chapter_number: str | int | None = None,
    page_number: int | None = None,
    cover_url: str = "",
    site_url: str = "",
) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO history (
                user_id,
                event_type,
                hq_id,
                title,
                chapter_id,
                chapter_number,
                page_number,
                cover_url,
                site_url,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(user_id),
                str(event_type or "view"),
                str(hq_id),
                str(title or "HQ"),
                str(chapter_id or ""),
                str(chapter_number or ""),
                int(page_number) if page_number else None,
                str(cover_url or ""),
                str(site_url or ""),
                _utc_now(),
            ),
        )


def count_history(user_id: int | str) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM history WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
    return int(row["total"] if row else 0)


def list_history(user_id: int | str, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, event_type, hq_id, title, chapter_id, chapter_number, page_number,
                   cover_url, site_url, created_at
            FROM history
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (str(user_id), max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    return [dict(row) for row in rows]

