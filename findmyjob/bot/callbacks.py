"""Ідентифікатори callback_data інлайн-кнопок.

Telegram обмежує callback_data 64 байтами, тому ідентифікатори короткі, а замість
повного посилання в них їде його хеш (`Vacancy.short_link`).
"""

from __future__ import annotations

import re

# Дії без параметрів
CB_VAC_1D = "vacancies_1d"
CB_VAC_14D = "vacancies_14d"
CB_CONFIRM_NO = "confirm_no"
CB_SHOW_HIDDEN = "show_hidden"                # фінальний крок — показ карток
CB_SHOW_HIDDEN_PROMPT = "show_hidden_prompt"  # проміжний — кількість + підтвердження
CB_CLEAR = "clear_history"     # запит підтвердження
CB_CLEAR_YES = "clear_yes"
CB_CLEAR_NO = "clear_no"
CB_CLEAR_HIDE = "clear_hidden"                # запит підтвердження
CB_CLEAR_HIDE_YES = "clear_hidden_yes"
CB_CLEAR_HIDE_NO = "clear_hidden_no"
CB_SHOW_FAVS = "show_favorites"
CB_FAVS_YES = "favs_yes"
CB_FAVS_NO = "favs_no"
CB_CAT_CONFIRM = "cat_confirm"
CB_NOOP = "noop"                              # кнопка-індикатор сторінки
CB_RESELECT_CATS = "reselect_cats"
CB_INTRO_CONTINUE = "intro_continue"
CB_NOTIFY_SETUP = "notify_setup"              # почати вибір категорій сповіщень
CB_NOTIFY_CONFIRM = "notify_confirm"          # "Додати нотифікацію"
CB_NOTIFY_OFF = "notify_off"                  # запит підтвердження
CB_NOTIFY_OFF_YES = "notify_off_yes"
CB_NOTIFY_OFF_NO = "notify_off_no"
CB_NDA_SHOW_NEW_YES = "nda_new_yes"
CB_NDA_SHOW_NEW_NO = "nda_new_no"
CB_NDA_SHOW_ALL_YES = "nda_all_yes"
CB_NDA_SHOW_ALL_NO = "nda_all_no"
CB_NDA_BASELINE_YES = "nda_baseline_yes"
CB_NDA_BASELINE_NO = "nda_baseline_no"

# Дії з параметром — префікси, до яких дописується значення
CB_CONFIRM_YES = "confirm_yes:"
CB_HIDE = "hide:"
CB_UNHIDE = "unhide:"
CB_RESTORE = "restore:"
CB_FAVORITE = "fav:"
CB_UNFAVORITE = "unfav:"
CB_FAV_DELETE = "fav_del:"    # видалити з обраних при перегляді списку
CB_CAT_TOGGLE = "cat_toggle:"
CB_CAT_PAGE = "cat_page:"
# Вибір категорій для сповіщень має власні префікси: інакше він затирав би
# категорії пошуку, бо клавіатура та сама.
CB_NOTIFY_TOGGLE = "ntf_toggle:"
CB_NOTIFY_PAGE = "ntf_page:"


def payload(prefix: str, value) -> str:
    """Складає callback_data з префікса та значення."""
    return f"{prefix}{value}"


def argument(callback_data: str, prefix: str) -> str:
    """Дістає значення з callback_data, відкидаючи префікс."""
    return callback_data[len(prefix):]


def exact(callback_data: str) -> str:
    """Regex, що збігається лише з цим callback_data повністю."""
    return f"^{re.escape(callback_data)}$"


def prefixed(prefix: str) -> str:
    """Regex, що збігається з будь-яким callback_data із цим префіксом."""
    return f"^{re.escape(prefix)}"
