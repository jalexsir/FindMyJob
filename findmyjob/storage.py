"""Персистентне сховище (SQLite) для "Обране" та "Вилучені з пошуку".

Точкове: знає лише про ці два списки, нічого не знає про решту структури
bot_data (кеш показаних вакансій, seen-хеші, обрані категорії — це й далі
живе тільки в пам'яті процесу й скидається при перезапуску).

Стратегія: бот тримає hidden/favorites у bot_data (швидкий доступ у пам'яті), а
при кожній зміні повністю перезаписує відповідні рядки таблиці для юзера.
При старті все один раз підвантажується з БД у bot_data.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

UserRecords = dict[str, dict]
AllRecords = dict[int, UserRecords]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    user_id      INTEGER NOT NULL,
    short_link   TEXT    NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    source       TEXT    NOT NULL DEFAULT '',
    link         TEXT    NOT NULL DEFAULT '',
    published    TEXT    NOT NULL DEFAULT '',
    pub_dt       TEXT,
    company      TEXT    NOT NULL DEFAULT '',
    location     TEXT    NOT NULL DEFAULT '',
    salary       TEXT    NOT NULL DEFAULT '',
    vacancy_hash TEXT    NOT NULL DEFAULT '',
    category     TEXT    NOT NULL DEFAULT '',
    categories   TEXT    NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, short_link)
);
"""

_COLUMNS = (
    "short_link", "title", "source", "link", "published", "pub_dt",
    "company", "location", "salary", "vacancy_hash", "category", "categories",
)
_INSERT_COLUMNS = ("user_id",) + _COLUMNS

_TABLE_HIDDEN = "hidden"
_TABLE_FAVORITES = "favorites"


class VacancyStore:
    """Доступ до SQLite-таблиць `hidden` і `favorites`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Схема ────────────────────────────────────────────────────────────────

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_SCHEMA.format(table=_TABLE_HIDDEN))
            conn.execute(_SCHEMA.format(table=_TABLE_FAVORITES))

    # ── Публічний API ────────────────────────────────────────────────────────

    def replace_hidden(self, user_id: int, data: UserRecords) -> None:
        self._replace_table(_TABLE_HIDDEN, user_id, data)

    def replace_favorites(self, user_id: int, data: UserRecords) -> None:
        self._replace_table(_TABLE_FAVORITES, user_id, data)

    def load_all_hidden(self) -> AllRecords:
        return self._load_all(_TABLE_HIDDEN)

    def load_all_favorites(self) -> AllRecords:
        return self._load_all(_TABLE_FAVORITES)

    # ── Внутрішні деталі ─────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _replace_table(self, table: str, user_id: int, data: UserRecords) -> None:
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            if data:
                placeholders = ", ".join("?" * len(_INSERT_COLUMNS))
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(_INSERT_COLUMNS)}) VALUES ({placeholders})",
                    list(self._rows(user_id, data)),
                )

    def _load_all(self, table: str) -> AllRecords:
        result: AllRecords = {}
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT user_id, {', '.join(_COLUMNS)} FROM {table}")
            for user_id, *row in cursor.fetchall():
                short_link, entry = _row_to_entry(row)
                result.setdefault(user_id, {})[short_link] = entry
        return result

    @staticmethod
    def _rows(user_id: int, data: UserRecords) -> Iterable[tuple]:
        for short_link, entry in data.items():
            yield _entry_to_row(user_id, short_link, entry)


def _entry_to_row(user_id: int, short_link: str, entry: dict) -> tuple:
    pub_dt = entry.get("pub_dt")
    return (
        user_id,
        short_link,
        entry.get("title", ""),
        entry.get("source", ""),
        entry.get("link", ""),
        entry.get("published", ""),
        pub_dt.isoformat() if isinstance(pub_dt, datetime) else None,
        entry.get("company", ""),
        entry.get("location", ""),
        entry.get("salary", ""),
        entry.get("vacancy_hash", ""),
        entry.get("category", ""),
        json.dumps(entry.get("categories", [])),
    )


def _row_to_entry(row: list) -> tuple[str, dict]:
    (short_link, title, source, link, published, pub_dt_str,
     company, location, salary, vacancy_hash, category, categories_json) = row
    entry = {
        "title": title,
        "source": source,
        "link": link,
        "published": published,
        "pub_dt": datetime.fromisoformat(pub_dt_str) if pub_dt_str else None,
        "company": company,
        "location": location,
        "salary": salary,
        "vacancy_hash": vacancy_hash,
        "category": category,
        "categories": json.loads(categories_json) if categories_json else [],
    }
    return short_link, entry
