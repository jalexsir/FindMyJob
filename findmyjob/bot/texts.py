"""Тексти інтерфейсу та українська морфологія числівників."""

from __future__ import annotations

# ── Кнопки Reply Keyboard ─────────────────────────────────────────────────────
BTN_VAC_1D = "📅 Вакансії за 1 день"
BTN_VAC_7D = "📆 Вакансії за 7 днів"
BTN_VAC_ALL = "📋 Всі вакансії"
BTN_SHOW_HIDDEN = "🔍 Вилучені з пошуку"
BTN_FAVORITES = "⭐ Обране"
BTN_CLEAR = "🗑 Очистити листування"
BTN_CLEAR_HIDE = "🗑 Очистити список вилучених"

ALL_BUTTON_TEXTS = (
    BTN_VAC_1D, BTN_VAC_7D, BTN_VAC_ALL,
    BTN_SHOW_HIDDEN, BTN_FAVORITES, BTN_CLEAR, BTN_CLEAR_HIDE,
)

# ── Підписи інлайн-кнопок ─────────────────────────────────────────────────────
BTN_OPEN_VACANCY = "🔗 Відкрити вакансію"
BTN_ADD_FAVORITE = "⭐ В обране"
BTN_ADD_FAVORITE_ALT = "⭐ Додати в обране"
BTN_IN_FAVORITES = "💛 В обраному"
BTN_REMOVE_FAVORITE = "💔 Прибрати з обраних"
BTN_HIDE = "🙈 Не показувати"
BTN_RESTORE = "👁 Відновити до перегляду"
BTN_SHOW_HIDDEN_LIST = "🔍 Показати вилучені вакансії"
BTN_RESELECT_CATS = "🔄 Переобрати категорії пошуку"
BTN_YES = "✅ Так"
BTN_NO = "❌ Ні"

# ── Повідомлення ──────────────────────────────────────────────────────────────
MSG_LOADING = "⏳ Завантажую вакансії, зачекай..."
MSG_PICK_PERIOD = "Обери період доступних вакансій в меню"
MSG_PICK_CATEGORIES = "Оберіть одну або декілька категорій вакансій 👇"
MSG_PICK_AT_LEAST_ONE = "Оберіть хоча б одну категорію!"
MSG_NO_CATEGORY_SELECTED = "⚠️ Категорія не обрана, оберіть будь ласка з списку 👇"
MSG_SHOW_VACANCIES = "❓ <b>Показати вакансії?</b>"
MSG_NO_VACANCIES = "Вакансій не знайдено (або всі приховані)."
MSG_MAYBE_LATER = "👍 Добре, почекаємо оновлень!"
MSG_TICK_TOCK = "⏰ Часікі тікають!"
MSG_HIDDEN_EMPTY = "📭 Список вилучених вакансій порожній."
MSG_HIDDEN_ALREADY_EMPTY = "📭 Список пустий"
MSG_FAVORITES_EMPTY = "⭐ Список обраних вакансій порожній."
MSG_FAVORITES_EMPTY_SHORT = "⭐ Список обраних порожній."
MSG_FAVORITES_GONE = (
    "⭐ Обрані вакансії не знайдені в поточних джерелах "
    "(можливо, вже зняті з публікації)."
)
MSG_CLEARING = "🗑 Очищую..."
MSG_ADDED_TO_FAVORITES = "⭐ Додано в обране!"
MSG_REMOVED_FROM_FAVORITES = "💔 Видалено з обраного"
MSG_REMOVED_FROM_FAVORITES_LIST = "💔 Прибрано з обраних"
MSG_RESTORED = "✅ Вакансію відновлено"

DEFAULT_VACANCY_TITLE = "вакансія"


# ── Морфологія ────────────────────────────────────────────────────────────────

def pluralize_ua(n: int, one: str, few: str, many: str) -> str:
    """Обирає правильну форму слова за українськими правилами відмінювання.

    one  — для 1, 21, 31... (крім 11)
    few  — для 2-4, 22-24, 32-34...
    many — для 0, 5-20, 25-30...
    """
    last_two = abs(n) % 100
    if 11 <= last_two <= 14:
        return many
    last = last_two % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def vacancies_word(n: int) -> str:
    return pluralize_ua(n, "вакансія", "вакансії", "вакансій")


def duplicates_word(n: int) -> str:
    return pluralize_ua(n, "дублікат", "дублікати", "дублікатів")


def favorite_vacancies_phrase(n: int) -> str:
    """'обрану вакансію' / 'обрані вакансії' / 'обраних вакансій'."""
    return pluralize_ua(n, "обрану вакансію", "обрані вакансії", "обраних вакансій")


def period_phrase(days: int | None) -> str:
    return f"за останні {days} д." if days else "за весь час"


def categories_status(selected: list[str]) -> str:
    """Текст над клавіатурою вибору категорій."""
    if not selected:
        return MSG_PICK_CATEGORIES
    return (
        f"Ви обрали категорій: <b>{', '.join(selected)}</b>\n\n"
        "Натисни ▶️ Продовжити або обери ще."
    )


def categories_confirmed(selected: list[str]) -> str:
    return f"✅ Категорії обрано: <b>{', '.join(selected)}</b>\n\nМеню доступне внизу 👇"
