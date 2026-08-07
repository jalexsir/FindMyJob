"""Тексти інтерфейсу та українська морфологія числівників."""

from __future__ import annotations

# ── Кнопки Reply Keyboard ─────────────────────────────────────────────────────
BTN_VAC_1D = "📅 За 1 день"
BTN_VAC_7D = "📆 За 7 днів"
BTN_VAC_ALL = "📋 Всі вакансії"
BTN_SHOW_HIDDEN = "🔍 Вилучені"
BTN_FAVORITES = "⭐ Обране"
BTN_NOTIFICATIONS = "🔔 Сповіщення"
# Більше не кнопки нижнього меню — рідковживані дії, тепер інлайн під "⚙️ Ще"
# (build_more_menu_keyboard), callback_data лишається той самий (CB_CLEAR /
# CB_CLEAR_HIDE), тож самі підписи можна коротшати без зачіпання логіки.
BTN_CLEAR = "🧹 Очистити листування"
BTN_CLEAR_HIDE = "🧹 Очистити вилучені"
BTN_MORE = "⚙️ Ще"
BTN_NDA_SHOW_NEW = "🆕 Показати нові"
BTN_NDA_SHOW_ALL = "📋 Показати всі"
BTN_NDA_UPDATE_BASELINE = "🔄 Оновити еталон"
# Той самий підпис, що й серед інлайн-кнопок нижче — у nda_mode це кнопка
# постійної клавіатури (єдиний вихід із NDA-режиму), деінде — інлайн.
BTN_RESELECT_CATS = "🔄 Переобрати категорії пошуку"

ALL_BUTTON_TEXTS = (
    BTN_VAC_1D, BTN_VAC_7D, BTN_VAC_ALL,
    BTN_SHOW_HIDDEN, BTN_FAVORITES, BTN_NOTIFICATIONS, BTN_MORE,
    BTN_NDA_SHOW_NEW, BTN_NDA_SHOW_ALL, BTN_NDA_UPDATE_BASELINE, BTN_RESELECT_CATS,
)

# ── Підписи інлайн-кнопок ─────────────────────────────────────────────────────
BTN_OPEN_VACANCY = "🔗 Відкрити"
BTN_FIND_ON_DJINNI = "🔎 Знайти"
BTN_ADD_FAVORITE = "⭐ В обране"
BTN_ADD_FAVORITE_ALT = "⭐ Додати в обране"
BTN_IN_FAVORITES = "💛 В обраному"
BTN_REMOVE_FAVORITE = "💔 Прибрати з обраних"
BTN_HIDE = "🙈 Не показувати"
BTN_RESTORE = "👁 Відновити до перегляду"
BTN_SHOW_HIDDEN_LIST = "🔍 Показати вилучені вакансії"
BTN_RESET_CATEGORIES = "🗑 Скинути обрані категорії"
BTN_NOTIFY_SETUP = "⚙️ Налаштувати сповіщення"
BTN_NOTIFY_OFF = "🔕 Вимкнути сповіщення"
BTN_YES = "✅ Так"
BTN_NO = "❌ Ні"
BTN_CONTINUE = "▶️ Продовжити"

# ── Повідомлення ──────────────────────────────────────────────────────────────
MSG_INTRO = (
    "🤖 <b>FindMyJob Бот</b> — шукає свіжі вакансії в IT (з бронюванням) та в "
    "напрямку Defence-tech (теж із бронюванням), одразу з трьох джерел — "
    "DOU, Djinni та окремий агрегатор nda.in.ua, в одному місці й без "
    "дублікатів.\n\n"
    "<b>Як це працює:</b>\n"
    "1️⃣ Оберіть свої категорії — 34 напрямки плюс окрема NDA-All, до 5 "
    "одночасно\n"
    "2️⃣ Оберіть період — за 1 день, за 7 днів або всі вакансії\n"
    "3️⃣ Перегляньте зведення й підтвердіть — спершу покажу кількість "
    "(без дублікатів), потім самі картки\n\n"
    "<b>Що вміє бот:</b>\n"
    "⭐ Обране — вакансії зберігаються між сесіями\n"
    "🙈 Вилучені з пошуку — приховуй нецікаве\n"
    "🔔 Сповіщення — підпишись на категорії (включно з NDA-All), "
    "надсилатиму нові вакансії щогодини з 8:00 до 20:00, а для NDA-All — "
    "тричі на день\n"
    "🔄 Завжди можна переобрати або скинути обрані категорії\n\n"
    "Все, що позначено — обране, приховане, підписки — зберігається навіть "
    "після перезапуску."
)
MSG_INTRO_FOOTER = "Вище ти можеш ознайомитися зі стислим описом функцій боту."
MSG_MORE_MENU = "⚙️ Обери дію 👇"
MSG_LOADING = "⏳ Завантажую вакансії, зачекай..."
MSG_DEDUP_IN_PROGRESS = "🔀 Видалення дублікатів…"
MSG_FETCHING = "⏳ Запрошую інформацію..."
MSG_PICK_PERIOD = "Обери період доступних вакансій в меню"
MSG_PICK_NDA_ACTION = "Обери дію в меню 👇"
MSG_PICK_CATEGORIES = "Оберіть одну або декілька категорій вакансій 👇"
MSG_PICK_AT_LEAST_ONE = "Оберіть хоча б одну категорію!"
MSG_NO_CATEGORY_SELECTED = "⚠️ Категорія не обрана, оберіть будь ласка зі списку 👇"
MSG_SHOW_VACANCIES = "❓ <b>Показати вакансії?</b>"
MSG_NO_VACANCIES = "Вакансій не знайдено (або всі приховані)."
MSG_MAYBE_LATER = "👍 Добре, почекаємо оновлень!"
MSG_RESELECT_PROMPT = "🔎 Можеш переобрати категорії пошуку 👇"
MSG_CLEAR_CONFIRM = (
    "⚠️ Всі повідомлення в цьому чаті будуть очищені, але списки вилучених і "
    "обраних вакансій залишаться незмінними.\n\nПродовжити?"
)
MSG_NOTIFY_INTRO = (
    "🔔 Тут ти можеш налаштувати сповіщення на нові вакансії "
    "по обраних категоріях"
)
MSG_NOTIFY_PICK = "Оберіть одну або декілька категорій вакансій для сповіщення 👇"
MSG_NOTIFY_OFF_CONFIRM = (
    "⚠️ Сповіщення будуть вимкнені, а обрані категорії скинуті. Погоджуєтесь?"
)
MSG_NOTIFY_OFF = (
    "🔕 Сповіщення вимкнено, обрані категорії скинуто.\n\n"
    "Увімкнути назад можна в меню «Сповіщення»."
)
MSG_NOTIFY_OFF_CANCELLED = (
    "👌 Нічого не змінено — сповіщення лишаються увімкненими.\n\n"
    "Обрати категорії можна кнопками нижче 👇"
)
MSG_NDA_EXCLUSIVE = "🔒 NDA-All — окрема категорія, її не можна поєднувати з іншими"
MSG_NDA_UPDATE_BASELINE_CONFIRM = "⚠️ Оновити еталонний список поточним станом NDA-All?"
MSG_NDA_BASELINE_CANCELLED = "👌 Еталонний список не змінено."
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


def dedup_done(count: int) -> str:
    return f"🔀 Видалено {count} {duplicates_word(count)}"


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


def notifications_status(selected: list[str]) -> str:
    """Текст над клавіатурою вибору категорій для сповіщень."""
    if not selected:
        return MSG_NOTIFY_PICK
    return (
        f"Категорії для сповіщення: <b>{', '.join(selected)}</b>\n\n"
        "Натисни 🔔 Додати нотифікацію або обери ще."
    )


def notifications_intro(subscribed: list[str]) -> str:
    """Екран «Сповіщення»: підказка або поточна підписка."""
    if not subscribed:
        return MSG_NOTIFY_INTRO
    return (
        f"🔔 Сповіщення увімкнено для категорій: <b>{', '.join(subscribed)}</b>\n\n"
        "Перевіряю нові вакансії щогодини з 8:00 до 20:00."
    )


def notifications_sent(count: int) -> str:
    """Підсумок під пачкою карток. Без HTML — надсилається звичайним текстом."""
    new_word = pluralize_ua(count, "нова", "нові", "нових")
    return f"🔔 Є {count} {new_word} {vacancies_word(count)}"


def notifications_saved(selected: list[str]) -> str:
    return (
        f"✅ Сповіщення увімкнено для категорій: <b>{', '.join(selected)}</b>\n\n"
        "Щогодини з 8:00 до 20:00 надсилатиму нові вакансії за сьогодні."
    )


def nda_new_summary(count: int) -> str:
    """Підсумок діффу проти еталонного списку ("Показати нові")."""
    if not count:
        return "🆕 Нових вакансій за сьогодні не знайдено."
    return f"🆕 Знайдено {count} {vacancies_word(count)}, яких немає в еталонному списку."


def nda_all_summary(count: int) -> str:
    """Підсумок повного поточного списку NDA-All ("Показати всі")."""
    if not count:
        return "📋 NDA-All зараз порожній — вакансій не знайдено."
    return f"📋 Усього в NDA-All зараз {count} {vacancies_word(count)}."


def nda_baseline_updated(count: int) -> str:
    return f"✅ Еталонний список оновлено: {count} {vacancies_word(count)}."
