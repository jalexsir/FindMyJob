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

from telegram import Update
from telegram.ext import ContextTypes

from findmyjob.storage import ChatAnchors, VacancyStore

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
    """Стан діалогу в межах чату: опорні точки очищення й підготовлена вибірка.

    Опорні точки (`first_tracked_message_id`, `start_message_id`) переживають
    перезапуск процесу — вони дублюються в таблицю `chat_state`. Решта
    (підготовлена вибірка, сторінка категорій) — суто тимчасова і живе лише в
    пам'яті.

    Стан підвантажується з БД **ліниво**, при першому зверненні до чату: так ми
    не читаємо всі чати на старті, а `Application.chat_data` у PTB і не дає
    записати себе ззовні.
    """

    KEY_FIRST_TRACKED = "first_tracked_message_id"
    KEY_START_MESSAGE_ID = "start_message_id"
    KEY_PENDING = "pending_vacancies"
    KEY_CATEGORY_PAGE = "cat_page"
    KEY_ANCHORS_LOADED = "anchors_loaded"

    def __init__(self, chat_data: dict[str, Any], chat_id: int, store: VacancyStore) -> None:
        self._chat_data = chat_data
        self._chat_id = chat_id
        self._store = store
        self._load_anchors()

    # ── Відстеження повідомлень (для "Очистити листування") ──────────────────
    # Видалення йде суцільним діапазоном ID, тож зберігати весь перелік
    # надісланих повідомлень не треба — достатньо його нижньої межі. Це і
    # прибирає необмежене зростання chat_data, і робить стан двома числами,
    # які тривіально покласти в БД.

    def track(self, *message_ids: int) -> None:
        """Опускає нижню межу діапазону, якщо повідомлення раніше за поточну."""
        if not message_ids:
            return
        lowest = min(message_ids)
        current = self.first_tracked_message_id
        if current is not None and current <= lowest:
            return
        self._chat_data[self.KEY_FIRST_TRACKED] = lowest
        self._save_anchors()

    @property
    def first_tracked_message_id(self) -> int | None:
        return self._chat_data.get(self.KEY_FIRST_TRACKED)

    def forget_tracked(self) -> None:
        """Скидає межу — усе, що було до цього, вже видалено."""
        if self.first_tracked_message_id is None:
            return
        self._chat_data[self.KEY_FIRST_TRACKED] = None
        self._save_anchors()

    @property
    def start_message_id(self) -> int | None:
        return self._chat_data.get(self.KEY_START_MESSAGE_ID)

    @start_message_id.setter
    def start_message_id(self, message_id: int) -> None:
        if self.start_message_id == message_id:
            return
        self._chat_data[self.KEY_START_MESSAGE_ID] = message_id
        self._save_anchors()

    # ── Персистентність опорних точок ────────────────────────────────────────

    def _load_anchors(self) -> None:
        if self._chat_data.get(self.KEY_ANCHORS_LOADED):
            return
        anchors = self._store.load_chat_anchors(self._chat_id)
        self._chat_data.setdefault(self.KEY_FIRST_TRACKED, anchors.first_tracked_message_id)
        self._chat_data.setdefault(self.KEY_START_MESSAGE_ID, anchors.start_message_id)
        self._chat_data[self.KEY_ANCHORS_LOADED] = True

    def _save_anchors(self) -> None:
        """Викликається лише коли опора реально змінилась — це рідко.

        На практиці: перше повідомлення в чаті, `/start` і по одному запису на
        кожне очищення. Звичайне надсилання картки в БД не пише.
        """
        self._store.save_chat_anchors(
            self._chat_id,
            ChatAnchors(
                first_tracked_message_id=self.first_tracked_message_id,
                start_message_id=self.start_message_id,
            ),
        )

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

    def chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> ChatSession:
        chat = update.effective_chat
        return ChatSession(context.chat_data, chat.id if chat else 0, self._store)
