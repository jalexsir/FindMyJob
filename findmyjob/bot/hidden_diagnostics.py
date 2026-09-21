"""Діагностичне логування звірки знайдених вакансій зі списком прихованих.

Фільтр прихованих порівнює лише `short_link` — це SHA-256(URL)[:16], тобто з
самого хеша не видно, чому вакансія збіглася чи не збіглася. Тому кожен рядок
логу друкує поруч із `short_link` те, з чого він пораховано (сирий `link`), а
для збігу — ще й `link`, збережений у списку прихованих: якщо вони різні, це
одразу видно.

Два незалежні перемикачі, бо в них різна ціна:

* `HIDDEN_DEBUG` — ручний пошук. Один рядок на кожну знайдену вакансію, але
  лише коли користувач сам запустив пошук.
* `HIDDEN_DEBUG_NOTIFIER` — погодинна розсилка. Проходить по кожному
  підписнику й кожній вакансії його категорій, тож за замовчуванням вимкнено.
"""

from __future__ import annotations

import logging
from typing import Mapping

from findmyjob.models import Vacancy

logger = logging.getLogger(__name__)

HIDDEN_DEBUG = True
HIDDEN_DEBUG_NOTIFIER = False


def _enabled(notifier: bool) -> bool:
    return HIDDEN_DEBUG_NOTIFIER if notifier else HIDDEN_DEBUG


def log_hidden_check_start(
    scope: str, user_id: int, found: int, hidden: int, *, notifier: bool = False
) -> None:
    """Початок звірки: скільки знайдених порівнюємо з яким розміром списку прихованих."""
    if not _enabled(notifier):
        return
    logger.info(
        "🔎 [ПРИХОВАНІ] %s, user-%s: звірка %d знайдених із %d прихованих",
        scope, user_id, found, hidden,
    )


def log_hidden_check(
    vacancy: Vacancy, hidden: Mapping[str, dict], *, notifier: bool = False
) -> None:
    """Один рядок на вакансію: вердикт, поля вакансії та short_link із сирим link.

    Для збігу додатково друкується `link` із запису в прихованих. `%r` замість
    `%s` навмисно: він показує пробіли на краях, невидимі символи й параметри
    URL, які й ламають збіг хешів.
    """
    if not _enabled(notifier):
        return

    short_link = vacancy.short_link
    entry = hidden.get(short_link)
    is_hidden = entry is not None

    stored = ""
    if is_hidden:
        stored_link = entry.get("link")
        stored = f" | у прихованих: link={stored_link!r}" if stored_link else (
            " | у прихованих: link відсутній (неповний запис)"
        )

    logger.info(
        "  %s | source=%r | category=%r | title=%r | company=%r | location=%r | "
        "published=%r | short_link=%s ← SHA-256 від link=%r%s",
        "🙈 ПРИХОВАНО" if is_hidden else "✅ ЛИШЕНО   ",
        vacancy.source, vacancy.category, vacancy.title, vacancy.company,
        vacancy.location, vacancy.published, short_link, vacancy.link, stored,
    )


def log_hidden_check_done(
    scope: str, user_id: int, found: int, hidden_matched: int, available: int,
    *, notifier: bool = False,
) -> None:
    """Підсумок звірки. `hidden_matched` — унікальні збіги, `available` — що лишилось.

    Для ручного пошуку `available` — уже після ліміту на джерело, для сповіщень —
    ще до звірки з журналом надісланого за день.
    """
    if not _enabled(notifier):
        return
    logger.info(
        "🔎 [ПРИХОВАНІ] %s, user-%s: знайдено %d, унікальних прихованих %d, "
        "лишилось %d",
        scope, user_id, found, hidden_matched, available,
    )
