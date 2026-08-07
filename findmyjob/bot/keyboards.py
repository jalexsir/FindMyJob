"""Побудова клавіатур Telegram."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
)

from findmyjob.feeds import AVAILABLE_CATEGORIES, NDA_CATEGORY, NDA_SOURCE_NAME, Site
from findmyjob.models import Vacancy

from . import callbacks as cb
from . import texts

# Категорій забагато, щоб влізти на один екран — по 2 в рядку з пагінацією
CATEGORIES_PER_PAGE = 10
CATEGORIES_PER_ROW = 2
# Більше — забагато RSS-запитів і занадто широке зведення
MAX_SELECTED_CATEGORIES = 5

# NDA-All в обох сценаріях — в кінці списку. У пошуку вона взаємовиключна з
# рештою (окремий конвеєр без RSS/дати публікації, спільний список — див.
# resolve_nda_toggle). У сповіщеннях — звичайна категорія в тій самій квоті:
# власний шкедулер (10:00/14:00/20:00) сам зводить її дифф проти спільного
# знімку незалежно від інших категорій користувача, тож поєднувати можна.
DISPLAY_CATEGORIES = AVAILABLE_CATEGORIES + [NDA_CATEGORY]


@dataclass(frozen=True)
class CategoryFlow:
    """Куди ведуть кнопки клавіатури категорій.

    Клавіатура одна на два сценарії — пошук і сповіщення, — але callback_data в
    них мають бути різні: інакше вибір категорій для сповіщень затирав би
    категорії пошуку.
    """

    toggle: str
    page: str
    confirm: str
    confirm_label: str
    reset: str
    categories: list[str]
    # Позначка 🔒 біля NDA-All має сенс лише там, де вона й справді
    # взаємовиключна (пошук) — у сповіщеннях це звичайна категорія.
    nda_locked_badge: bool = True


SEARCH_FLOW = CategoryFlow(
    toggle=cb.CB_CAT_TOGGLE,
    page=cb.CB_CAT_PAGE,
    confirm=cb.CB_CAT_CONFIRM,
    confirm_label="▶️ Продовжити",
    reset=cb.CB_CAT_RESET,
    categories=DISPLAY_CATEGORIES,
)
NOTIFY_FLOW = CategoryFlow(
    toggle=cb.CB_NOTIFY_TOGGLE,
    page=cb.CB_NOTIFY_PAGE,
    confirm=cb.CB_NOTIFY_CONFIRM,
    confirm_label="🔔 Додати нотифікацію",
    reset=cb.CB_NOTIFY_RESET,
    categories=DISPLAY_CATEGORIES,
    nda_locked_badge=False,
)


class CategoryToggleBlocked(Exception):
    """Клік по категорії відхилено — квота вичерпана або (лише в пошуку)
    зачеплено взаємовиключність NDA-All. `message` — текст для алерту."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def toggle_category(
    selected: list[str], category: str, *, nda_exclusive: bool
) -> list[str]:
    """Повертає НОВИЙ список вибору після кліку по категорії.

    Спільна для пошуку й сповіщень: обидва сценарії — той самий toggle з тим
    самим лімітом (`MAX_SELECTED_CATEGORIES`), різниця лише в
    `nda_exclusive`. У пошуку (`True`) NDA-All — самодостатній конвеєр без
    RSS/дати публікації, тож обрання скидає решту, а спроба додати щось
    інше, поки вона активна, блокується. У сповіщеннях (`False`) NDA-All —
    звичайна категорія в тій самій квоті: власний шкедулер сам зводить її
    дифф незалежно від решти обраного, поєднувати можна.

    Підіймає `CategoryToggleBlocked`, якщо клік має бути відхилений.
    """
    if nda_exclusive:
        if category == NDA_CATEGORY:
            return [] if category in selected else [NDA_CATEGORY]
        if NDA_CATEGORY in selected:
            raise CategoryToggleBlocked(texts.MSG_NDA_EXCLUSIVE)

    if category in selected:
        selected.remove(category)
        return selected
    if len(selected) >= MAX_SELECTED_CATEGORIES:
        raise CategoryToggleBlocked(
            f"Ви можете обрати тільки {MAX_SELECTED_CATEGORIES} категорій одночасно"
        )
    selected.append(category)
    return selected


def build_persistent_keyboard(
    nda_mode: bool = False, is_admin: bool = False, more_mode: bool = False
) -> ReplyKeyboardMarkup:
    """Головне меню, що завжди видно внизу.

    Верхній і нижній ряд підміняються залежно від режиму (лише один з двох
    може бути активним одночасно — `more_mode` не поєднується з `nda_mode`,
    виклики самі стежать за цим за станом обраних категорій):

    - `nda_mode=True` — обрана лише категорія NDA-All: верхній ряд замінюється
      на її дії ("1 день / 7 днів / всі вакансії" тут не мають сенсу — це один
      спільний список без дат публікації). `is_admin` додає туди ще й кнопку
      оновлення еталону — лише для власника бота. Нижній ряд отримує ще й
      "🔄 Переобрати категорії пошуку" — єдиний спосіб вийти з NDA-режиму,
      бо звичайні кнопки періоду тут не показуються.
    - `more_mode=True` — після "⚙️ Ще": верхній ряд стає рідковживаними діями
      (очищення), а нижній — самотньою кнопкою "◀️ Назад", яка повертає
      попередній вигляд (nda_mode чи звичайний — вирішує виклик).
    """
    if more_mode:
        top_row = [KeyboardButton(texts.BTN_CLEAR), KeyboardButton(texts.BTN_CLEAR_HIDE)]
        bottom_row = [KeyboardButton(texts.BTN_BACK)]
    else:
        if nda_mode:
            top_row = [
                KeyboardButton(texts.BTN_NDA_SHOW_NEW), KeyboardButton(texts.BTN_NDA_SHOW_ALL),
            ]
            if is_admin:
                top_row.append(KeyboardButton(texts.BTN_NDA_UPDATE_BASELINE))
            bottom_row = [KeyboardButton(texts.BTN_RESELECT_CATS), KeyboardButton(texts.BTN_MORE)]
        else:
            top_row = [
                KeyboardButton(texts.BTN_VAC_1D),
                KeyboardButton(texts.BTN_VAC_7D),
                KeyboardButton(texts.BTN_VAC_ALL),
            ]
            bottom_row = [KeyboardButton(texts.BTN_MORE)]

    return ReplyKeyboardMarkup(
        [
            top_row,
            [KeyboardButton(texts.BTN_SHOW_HIDDEN),
             KeyboardButton(texts.BTN_FAVORITES),
             KeyboardButton(texts.BTN_NOTIFICATIONS)],
            bottom_row,
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def build_category_keyboard(
    selected: list[str], page: int = 0, flow: CategoryFlow = SEARCH_FLOW
) -> InlineKeyboardMarkup:
    """Клавіатура вибору категорій із пагінацією.

    Позначки (✅/⬜) зберігаються при переході між сторінками, бо стан вибору не
    залежить від поточної сторінки.
    """
    total_pages = max(1, -(-len(flow.categories) // CATEGORIES_PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    start = page * CATEGORIES_PER_PAGE
    page_categories = flow.categories[start:start + CATEGORIES_PER_PAGE]

    rows = [
        [
            InlineKeyboardButton(
                _category_label(category, selected, flow.nda_locked_badge),
                callback_data=cb.payload(flow.toggle, category),
            )
            for category in page_categories[i:i + CATEGORIES_PER_ROW]
        ]
        for i in range(0, len(page_categories), CATEGORIES_PER_ROW)
    ]

    if total_pages > 1:
        rows.append(_pagination_row(page, total_pages, flow))

    if selected:
        rows.append([InlineKeyboardButton(
            texts.BTN_RESET_CATEGORIES, callback_data=flow.reset,
        )])
        rows.append([InlineKeyboardButton(
            f"{flow.confirm_label} ({len(selected)} обрано)",
            callback_data=flow.confirm,
        )])
    return InlineKeyboardMarkup(rows)


def _category_label(category: str, selected: list[str], show_lock: bool) -> str:
    mark = "✅" if category in selected else "⬜"
    suffix = " 🔒" if category == NDA_CATEGORY and show_lock else ""
    return f"{mark} {category}{suffix}"


def build_notification_footer_keyboard() -> InlineKeyboardMarkup:
    """Кнопка під кожним сповіщенням — вихід має бути під рукою, а не в меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_NOTIFY_OFF, callback_data=cb.CB_NOTIFY_OFF)],
    ])


def _yes_no_keyboard(yes_callback: str, no_callback: str) -> InlineKeyboardMarkup:
    """Стандартна пара Так/Ні (Ні зліва, Так справа) — усі підтвердження в
    боті виглядають однаково, тож будують клавіатуру через цей хелпер."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(texts.BTN_NO, callback_data=no_callback),
        InlineKeyboardButton(texts.BTN_YES, callback_data=yes_callback),
    ]])


def build_notify_off_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні на питання про вимкнення сповіщень."""
    return _yes_no_keyboard(cb.CB_NOTIFY_OFF_YES, cb.CB_NOTIFY_OFF_NO)


def build_clear_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні на питання про очищення листування."""
    return _yes_no_keyboard(cb.CB_CLEAR_YES, cb.CB_CLEAR_NO)


def build_clear_hidden_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні на питання про очищення списку вилучених."""
    return _yes_no_keyboard(cb.CB_CLEAR_HIDE_YES, cb.CB_CLEAR_HIDE_NO)


def build_notifications_keyboard(subscribed: bool) -> InlineKeyboardMarkup:
    """Екран «Сповіщення»: налаштувати, а якщо підписка вже є — ще й вимкнути."""
    rows = [[InlineKeyboardButton(texts.BTN_NOTIFY_SETUP, callback_data=cb.CB_NOTIFY_SETUP)]]
    if subscribed:
        rows.append(
            [InlineKeyboardButton(texts.BTN_NOTIFY_OFF, callback_data=cb.CB_NOTIFY_OFF)]
        )
    return InlineKeyboardMarkup(rows)


def _pagination_row(
    page: int, total_pages: int, flow: CategoryFlow = SEARCH_FLOW
) -> list[InlineKeyboardButton]:
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️", callback_data=cb.payload(flow.page, page - 1)))
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=cb.CB_NOOP))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("▶️", callback_data=cb.payload(flow.page, page + 1)))
    return row


# Розділювач між посадою і додатковим уточненням у заголовку, напр.
# "Technical Support Engineer — RnD". Лише варіанти з пробілами з обох боків
# (" — ", " - ") — щоб не різати дефіс усередині самої назви ("C-level").
_TITLE_SUFFIX_RE = re.compile(r"\s[—–−|]\s|\s-\s")


def _search_title(title: str) -> str:
    """Ліва частина посади до розділового знака — саме вона йде в пошук."""
    match = _TITLE_SUFFIX_RE.search(title)
    return title[:match.start()].strip() if match else title.strip()


def _djinni_search_url(vacancy: Vacancy) -> str:
    """Google-пошук цієї ж вакансії на Djinni за назвою й компанією.

    DOU не показує компанію структуровано на власній сторінці вакансії так
    зручно, як хотілось би, тож для вакансій із DOU даємо швидкий шлях
    перевірити, чи є той самий запис на Djinni.
    """
    query = f'site:djinni.co inurl:jobs "{_search_title(vacancy.title)}" {vacancy.company}'
    return f"https://www.google.com/search?q={quote(query)}"


def _open_vacancy_row(vacancy: Vacancy) -> list[InlineKeyboardButton]:
    """Рядок з посиланням на вакансію, а для DOU — ще й пошуком на Djinni зліва."""
    row = []
    if vacancy.source.startswith(Site.DOU.value):
        row.append(InlineKeyboardButton(texts.BTN_FIND_ON_DJINNI, url=_djinni_search_url(vacancy)))
    row.append(InlineKeyboardButton(texts.BTN_OPEN_VACANCY, url=vacancy.link))
    return row


def build_vacancy_keyboard(vacancy: Vacancy, is_favorite: bool = False) -> InlineKeyboardMarkup:
    """Клавіатура під карткою вакансії у звичайному списку."""
    short_link = vacancy.short_link
    favorite_button = InlineKeyboardButton(
        texts.BTN_IN_FAVORITES if is_favorite else texts.BTN_ADD_FAVORITE,
        callback_data=cb.payload(
            cb.CB_UNFAVORITE if is_favorite else cb.CB_FAVORITE, short_link
        ),
    )
    if vacancy.source == NDA_SOURCE_NAME:
        # NDA-All — один спільний еталонний список, не персональна вибірка за
        # категоріями: "Не показувати" тут ховати нема від чого (переобрати
        # категорії, щоб вакансія зникла, не можна — вона єдина для всіх),
        # тож лишаємо тільки Обране й Відкрити в одному рядку.
        return InlineKeyboardMarkup([
            [favorite_button, InlineKeyboardButton(texts.BTN_OPEN_VACANCY, url=vacancy.link)],
        ])
    return InlineKeyboardMarkup([
        _open_vacancy_row(vacancy),
        [favorite_button,
         InlineKeyboardButton(texts.BTN_HIDE, callback_data=cb.payload(cb.CB_HIDE, short_link))],
    ])


def build_favorite_vacancy_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    """Клавіатура для вакансії зі списку Обраних — з кнопкою видалення."""
    short_link = vacancy.short_link
    return InlineKeyboardMarkup([
        _open_vacancy_row(vacancy),
        [InlineKeyboardButton(texts.BTN_REMOVE_FAVORITE,
                              callback_data=cb.payload(cb.CB_FAV_DELETE, short_link))],
        [InlineKeyboardButton(texts.BTN_HIDE,
                              callback_data=cb.payload(cb.CB_HIDE, short_link))],
    ])


def build_restore_keyboard(short_link: str) -> InlineKeyboardMarkup:
    """Кнопка відновлення для картки зі списку вилучених."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_RESTORE,
                              callback_data=cb.payload(cb.CB_RESTORE, short_link))],
    ])


def build_unhide_keyboard(short_link: str) -> InlineKeyboardMarkup:
    """Кнопка повернення щойно прихованої вакансії до перегляду."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_RESTORE,
                              callback_data=cb.payload(cb.CB_UNHIDE, short_link))],
    ])


def build_show_hidden_prompt_keyboard() -> InlineKeyboardMarkup:
    """Підтвердження показу списку вилучених."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_SHOW_HIDDEN_LIST, callback_data=cb.CB_SHOW_HIDDEN)],
    ])


def build_intro_continue_keyboard() -> InlineKeyboardMarkup:
    """Єдина кнопка «Продовжити» під інтро — веде до вибору категорій."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_CONTINUE, callback_data=cb.CB_INTRO_CONTINUE)],
    ])


def build_reselect_keyboard() -> InlineKeyboardMarkup:
    """Єдина кнопка «Переобрати категорії пошуку»."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_RESELECT_CATS, callback_data=cb.CB_RESELECT_CATS)],
    ])


def build_no_vacancies_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура для "нічого не знайдено": глянути вилучені (спочатку кількість +
    підтвердження) АБО переобрати категорії."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(texts.BTN_SHOW_HIDDEN_LIST,
                              callback_data=cb.CB_SHOW_HIDDEN_PROMPT)],
        [InlineKeyboardButton(texts.BTN_RESELECT_CATS, callback_data=cb.CB_RESELECT_CATS)],
    ])


def build_confirm_show_keyboard(days: int | None) -> InlineKeyboardMarkup:
    """Так/Ні під зведенням + можливість переобрати категорії."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(texts.BTN_NO, callback_data=cb.CB_CONFIRM_NO),
            InlineKeyboardButton(
                texts.BTN_YES,
                callback_data=cb.payload(cb.CB_CONFIRM_YES, days if days is not None else "all"),
            ),
        ],
        [InlineKeyboardButton(texts.BTN_RESELECT_CATS, callback_data=cb.CB_RESELECT_CATS)],
    ])


def build_favorites_confirm_keyboard() -> InlineKeyboardMarkup:
    return _yes_no_keyboard(cb.CB_FAVS_YES, cb.CB_FAVS_NO)


def build_nda_new_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні під підсумком діффу ("Показати нові")."""
    return _yes_no_keyboard(cb.CB_NDA_SHOW_NEW_YES, cb.CB_NDA_SHOW_NEW_NO)


def build_nda_all_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні під підсумком повного списку ("Показати всі")."""
    return _yes_no_keyboard(cb.CB_NDA_SHOW_ALL_YES, cb.CB_NDA_SHOW_ALL_NO)


def build_nda_baseline_confirm_keyboard() -> InlineKeyboardMarkup:
    """Так/Ні під запитом на оновлення еталону (лише адмін)."""
    return _yes_no_keyboard(cb.CB_NDA_BASELINE_YES, cb.CB_NDA_BASELINE_NO)


def replace_button(
    markup: InlineKeyboardMarkup, target_callback_data: str, replacement: InlineKeyboardButton
) -> InlineKeyboardMarkup:
    """Повертає копію клавіатури, де кнопку з заданим callback_data замінено."""
    return InlineKeyboardMarkup([
        [replacement if button.callback_data == target_callback_data else button
         for button in row]
        for row in markup.inline_keyboard
    ])
