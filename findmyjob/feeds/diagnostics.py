"""Діагностичне логування завантаження та дедуплікації фідів.

Два незалежні перемикачі, бо в них дуже різна ціна:

* `FEED_DEBUG` — повний дамп сирого RSS XML на кожен фід. Вмикай тільки для
  дебагу: на кожен запит користувача це десятки-сотні рядків у лог.
* `DEDUP_DEBUG` — етапи дедуплікації (списки перед злиттям, які саме дублікати
  видалено, фінальний результат). Значно вужче й тихіше, увімкнено за
  замовчуванням.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover — лише для анотацій
    from findmyjob.models import Vacancy

logger = logging.getLogger(__name__)

FEED_DEBUG = False
DEDUP_DEBUG = True


def log_feed(message: str, *args) -> None:
    """Детальне логування RSS-запитів/парсингу."""
    if FEED_DEBUG:
        logger.info(message, *args)


def log_dedup(message: str, *args) -> None:
    """Логування етапів дедуплікації."""
    if DEDUP_DEBUG:
        logger.info(message, *args)


def log_vacancy_list(name: str, vacancies: Sequence["Vacancy"]) -> None:
    if not DEDUP_DEBUG:
        return
    logger.info("📋 %s (%d вак.):", name, len(vacancies))
    for i, vacancy in enumerate(vacancies, 1):
        logger.info("  %2d. title=%r, company=%r", i, vacancy.title, vacancy.company)
    if not vacancies:
        logger.info("  (порожній)")


def log_duplicate_pair(context_label: str, kept: "Vacancy", removed: "Vacancy") -> None:
    """Логує ОБИДВІ вакансії пари-дубліката (з посиланнями) ще до видалення."""
    if not DEDUP_DEBUG:
        return
    logger.info(
        "  ✂ ДУБЛІКАТ %s:\n"
        "      залишено: title=%r, company=%r, link=%s\n"
        "      видалено: title=%r, company=%r, link=%s",
        context_label,
        kept.title, kept.company, kept.link,
        removed.title, removed.company, removed.link,
    )
