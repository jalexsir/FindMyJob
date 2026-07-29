"""Персистентне сховище (SQLite).

Зберігає лише те, що обов'язково має пережити перезапуск процесу:

* `hidden` / `favorites` — персональні списки вакансій;
* `chat_state` — опорні точки для «Очистити листування» (без них після
  рестарту бот не знає, з якого повідомлення починати видалення).

Решта (кеш показаних вакансій, seen-хеші, обрані категорії) — похідні дані,
які дешево відновити, тож вони свідомо живуть лише в пам'яті процесу.

Стратегія для списків: бот тримає hidden/favorites у bot_data (швидкий доступ
у пам'яті), а при кожній зміні повністю перезаписує відповідні рядки таблиці
для юзера. При старті все один раз підвантажується з БД у bot_data.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
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

_CHAT_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_state (
    chat_id                  INTEGER PRIMARY KEY,
    first_tracked_message_id INTEGER,
    start_message_id         INTEGER
);
"""

_NOTIFICATION_SUBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_subs (
    user_id    INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    categories TEXT    NOT NULL,
    created_at TEXT
);
"""

# Журнал надісланого за день. Ключ (user_id, day, short_link) робить повторне
# надсилання неможливим за побудовою, а `day` — очищення тривіальним.
_NOTIFICATION_SENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_sent (
    user_id    INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    short_link TEXT    NOT NULL,
    PRIMARY KEY (user_id, day, short_link)
);
"""

_TABLE_HIDDEN = "hidden"
_TABLE_FAVORITES = "favorites"
_TABLE_CHAT_STATE = "chat_state"
_TABLE_NOTIFICATION_SUBS = "notification_subs"
_TABLE_NOTIFICATION_SENT = "notification_sent"


@dataclass(frozen=True)
class ChatAnchors:
    """Опорні точки чату для «Очистити листування».

    Видалення йде суцільним діапазоном ID, тому достатньо запам'ятати два числа,
    а не весь перелік надісланих повідомлень:

    * `first_tracked_message_id` — найраніше повідомлення, яке ще треба прибрати
      (нижня межа діапазону); None — відстежувати ще нема чого;
    * `start_message_id` — повідомлення `/start`, яке слугує якорем і навмисно
      переживає очищення.
    """

    first_tracked_message_id: int | None = None
    start_message_id: int | None = None


@dataclass(frozen=True)
class NotificationSub:
    """Підписка користувача на сповіщення про нові вакансії.

    `chat_id` зберігається разом із підпискою, бо шкедулер працює без апдейта —
    взяти чат із `update.effective_chat` там немає звідки.
    """

    user_id: int
    chat_id: int
    categories: list[str]


class VacancyStore:
    """Доступ до SQLite-таблиць `hidden`, `favorites`, `chat_state`
    і `notification_subs`."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    # ── Схема ────────────────────────────────────────────────────────────────

    def init_db(self) -> None:
        """Створює таблиці, якщо їх ще немає. Безпечно на наявній БД."""
        with self._connect() as conn:
            conn.execute(_SCHEMA.format(table=_TABLE_HIDDEN))
            conn.execute(_SCHEMA.format(table=_TABLE_FAVORITES))
            conn.execute(_CHAT_STATE_SCHEMA)
            conn.execute(_NOTIFICATION_SUBS_SCHEMA)
            conn.execute(_NOTIFICATION_SENT_SCHEMA)

    # ── Списки вакансій ──────────────────────────────────────────────────────

    def replace_hidden(self, user_id: int, data: UserRecords) -> None:
        self._replace_table(_TABLE_HIDDEN, user_id, data)

    def replace_favorites(self, user_id: int, data: UserRecords) -> None:
        self._replace_table(_TABLE_FAVORITES, user_id, data)

    def load_all_hidden(self) -> AllRecords:
        return self._load_all(_TABLE_HIDDEN)

    def load_all_favorites(self) -> AllRecords:
        return self._load_all(_TABLE_FAVORITES)

    # ── Опорні точки чату ────────────────────────────────────────────────────

    def load_chat_anchors(self, chat_id: int) -> ChatAnchors:
        """Порожні опори для невідомого чату — це нормальний стан, не помилка."""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT first_tracked_message_id, start_message_id "
                f"FROM {_TABLE_CHAT_STATE} WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        if row is None:
            return ChatAnchors()
        return ChatAnchors(first_tracked_message_id=row[0], start_message_id=row[1])

    def save_chat_anchors(self, chat_id: int, anchors: ChatAnchors) -> None:
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {_TABLE_CHAT_STATE} "
                f"(chat_id, first_tracked_message_id, start_message_id) VALUES (?, ?, ?) "
                f"ON CONFLICT(chat_id) DO UPDATE SET "
                f"first_tracked_message_id = excluded.first_tracked_message_id, "
                f"start_message_id = excluded.start_message_id",
                (chat_id, anchors.first_tracked_message_id, anchors.start_message_id),
            )

    # ── Підписки на сповіщення ───────────────────────────────────────────────

    def save_subscription(self, user_id: int, chat_id: int, categories: list[str]) -> None:
        """Створює або переписує підписку — на користувача вона рівно одна."""
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {_TABLE_NOTIFICATION_SUBS} "
                f"(user_id, chat_id, categories, created_at) VALUES (?, ?, ?, ?) "
                f"ON CONFLICT(user_id) DO UPDATE SET "
                f"chat_id = excluded.chat_id, categories = excluded.categories",
                (user_id, chat_id, json.dumps(categories), datetime.now().isoformat()),
            )

    def delete_subscription(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM {_TABLE_NOTIFICATION_SUBS} WHERE user_id = ?", (user_id,)
            )

    def load_all_subscriptions(self) -> dict[int, NotificationSub]:
        """Усі підписки — шкедулер читає їх один раз при старті."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT user_id, chat_id, categories FROM {_TABLE_NOTIFICATION_SUBS}"
            ).fetchall()
        return {
            user_id: NotificationSub(user_id, chat_id, _load_categories(raw))
            for user_id, chat_id, raw in rows
        }

    # ── Журнал надісланих сповіщень ──────────────────────────────────────────

    def sent_today(self, user_id: int, day: str) -> set[str]:
        """Що цьому користувачу вже надіслано сьогодні."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT short_link FROM {_TABLE_NOTIFICATION_SENT} "
                f"WHERE user_id = ? AND day = ?",
                (user_id, day),
            ).fetchall()
        return {row[0] for row in rows}

    def mark_sent(self, user_id: int, day: str, short_links: Iterable[str]) -> None:
        """Журнал накопичувальний: те, що вже надіслано, лишається до кінця дня.

        Саме тому надіслане не «віднімається» від наступної вибірки, а додається
        сюди — інакше вакансія з попередньої години наступного разу знову
        виглядала б новою.
        """
        rows = [(user_id, day, link) for link in short_links]
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                f"INSERT OR IGNORE INTO {_TABLE_NOTIFICATION_SENT} "
                f"(user_id, day, short_link) VALUES (?, ?, ?)",
                rows,
            )

    def purge_sent_before(self, day: str) -> int:
        """Прибирає журнал за попередні дні. Повертає кількість видалених рядків."""
        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {_TABLE_NOTIFICATION_SENT} WHERE day < ?", (day,)
            )
            return cursor.rowcount

    def clear_sent(self, user_id: int) -> None:
        """Повне очищення журналу користувача — при вимкненні сповіщень."""
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM {_TABLE_NOTIFICATION_SENT} WHERE user_id = ?", (user_id,)
            )

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


def _load_categories(raw: str) -> list[str]:
    """Битий JSON у колонці не має валити старт бота — краще порожній список."""
    try:
        categories = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return categories if isinstance(categories, list) else []


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
