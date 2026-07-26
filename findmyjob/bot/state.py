"""Стан користувача та діалогу поверх `bot_data` / `chat_data`.

`bot_data` — це звичайний словник PTB, спільний для всього застосунку, тому все
в ньому розкладено по per-user ключах. Ці класи — єдине місце, де ці ключі
формуються: решта коду працює з іменованими властивостями.

Персистентність точкова: у SQLite їдуть лише "Вилучені" та "Обране". Кеш
показаних вакансій, seen-хеші й обрані категорії живуть тільки в пам'яті процесу
і скидаються при перезапуску.
"""

from __future__ import annotations

from typing import Any

from telegram.ext import ContextTypes

from findmyjob.storage import VacancyStore

HIDDEN_KEY_PREFIX = "hidden_"
FAVORITES_KEY_PREFIX = "favorites_"
SEEN_KEY_PREFIX = "seen_"
VACANCY_CACHE_KEY_PREFIX = "vcache_"
CATEGORIES_KEY_PREFIX = "categories_"


def hidden_key(user_id: int) -> str:
    return f"{HIDDEN_KEY_PREFIX}{user_id}"


def favorites_key(user_id: int) -> str:
    return f"{FAVORITES_KEY_PREFIX}{user_id}"


class UserState:
    """Дані одного користувача: вилучені, обране, переглянуте, категорії."""

    def __init__(self, bot_data: dict[str, Any], user_id: int, store: VacancyStore) -> None:
        self._bot_data = bot_data
        self._user_id = user_id
        self._store = store

    @property
    def user_id(self) -> int:
        return self._user_id

    # ── Вилучені з пошуку ────────────────────────────────────────────────────

    @property
    def hidden(self) -> dict[str, dict]:
        """{short_link: {"title": ..., ...повні поля вакансії, якщо є в кеші}}."""
        return self._bot_data.setdefault(hidden_key(self._user_id), {})

    def save_hidden(self, data: dict[str, dict] | None = None) -> None:
        """Записує вилучені в bot_data і в БД. Без аргументу — зберігає поточні."""
        if data is not None:
            self._bot_data[hidden_key(self._user_id)] = data
        self._store.replace_hidden(self._user_id, self.hidden)

    def hidden_for_categories(self, categories: list[str]) -> dict[str, dict]:
        """Вилучені лише обраних зараз категорій.

        Глобальний список міг накопичитись, поки були обрані інші категорії —
        показувати його цілком було б несподівано.
        """
        if not categories:
            return self.hidden
        selected = set(categories)
        return {
            short_link: entry
            for short_link, entry in self.hidden.items()
            if selected & set(entry.get("categories", []))
        }

    # ── Обране ───────────────────────────────────────────────────────────────

    @property
    def favorites(self) -> dict[str, dict]:
        """{short_link: vacancy_dict}."""
        return self._bot_data.setdefault(favorites_key(self._user_id), {})

    def save_favorites(self, data: dict[str, dict] | None = None) -> None:
        if data is not None:
            self._bot_data[favorites_key(self._user_id)] = data
        self._store.replace_favorites(self._user_id, self.favorites)

    # ── Кеш повних даних показаних вакансій ──────────────────────────────────
    # Дозволяє hide/favorite/restore працювати з уже наявними даними, без
    # повторного фетчу RSS.

    @property
    def vacancy_cache(self) -> dict[str, dict]:
        return self._bot_data.setdefault(f"{VACANCY_CACHE_KEY_PREFIX}{self._user_id}", {})

    def cache_vacancy(self, short_link: str, data: dict) -> None:
        self.vacancy_cache[short_link] = data

    def cached_vacancy(self, short_link: str) -> dict | None:
        return self.vacancy_cache.get(short_link)

    # ── Переглянуті вакансії (мітка NEW) ─────────────────────────────────────

    @property
    def seen(self) -> set[str]:
        return self._bot_data.setdefault(f"{SEEN_KEY_PREFIX}{self._user_id}", set())

    def mark_seen(self, hashes: list[str]) -> None:
        if hashes:
            self.seen.update(hashes)

    # ── Обрані категорії ─────────────────────────────────────────────────────

    @property
    def categories(self) -> list[str]:
        return self._bot_data.get(f"{CATEGORIES_KEY_PREFIX}{self._user_id}", [])

    def set_categories(self, categories: list[str]) -> None:
        self._bot_data[f"{CATEGORIES_KEY_PREFIX}{self._user_id}"] = categories


class ChatSession:
    """Стан діалогу в межах чату: надіслані повідомлення й підготовлена вибірка."""

    KEY_MESSAGE_IDS = "message_ids"
    KEY_PENDING = "pending_vacancies"
    KEY_CATEGORY_PAGE = "cat_page"
    KEY_START_MESSAGE_ID = "start_message_id"

    def __init__(self, chat_data: dict[str, Any]) -> None:
        self._chat_data = chat_data

    # ── Відстеження повідомлень (для "Очистити листування") ──────────────────

    def track(self, *message_ids: int) -> None:
        self._chat_data.setdefault(self.KEY_MESSAGE_IDS, []).extend(message_ids)

    @property
    def tracked_message_ids(self) -> list[int]:
        return self._chat_data.get(self.KEY_MESSAGE_IDS, [])

    def forget_tracked(self) -> None:
        self._chat_data[self.KEY_MESSAGE_IDS] = []

    @property
    def start_message_id(self) -> int | None:
        return self._chat_data.get(self.KEY_START_MESSAGE_ID)

    @start_message_id.setter
    def start_message_id(self, message_id: int) -> None:
        self._chat_data[self.KEY_START_MESSAGE_ID] = message_id

    # ── Сторінка вибору категорій ────────────────────────────────────────────

    @property
    def category_page(self) -> int:
        return self._chat_data.get(self.KEY_CATEGORY_PAGE, 0)

    @category_page.setter
    def category_page(self, page: int) -> None:
        self._chat_data[self.KEY_CATEGORY_PAGE] = page

    # ── Підготовлена вибірка вакансій ────────────────────────────────────────
    # Заповнюється на кроці зведення, показується після підтвердження — щоб не
    # фетчити RSS удруге.

    @property
    def pending_vacancies(self) -> list[dict]:
        return self._chat_data.get(self.KEY_PENDING, [])

    @pending_vacancies.setter
    def pending_vacancies(self, vacancies: list[dict]) -> None:
        self._chat_data[self.KEY_PENDING] = vacancies

    def clear_pending(self) -> None:
        self._chat_data.pop(self.KEY_PENDING, None)


class StateRepository:
    """Фабрика станів — єдина точка, що знає про `VacancyStore`."""

    def __init__(self, store: VacancyStore) -> None:
        self._store = store

    @property
    def store(self) -> VacancyStore:
        return self._store

    def user(self, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> UserState:
        return UserState(context.bot_data, user_id, self._store)

    @staticmethod
    def chat(context: ContextTypes.DEFAULT_TYPE) -> ChatSession:
        return ChatSession(context.chat_data)
