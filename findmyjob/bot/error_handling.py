"""Централізований обробник помилок PTB (`Application.add_error_handler`).

Без нього PTB сам ловить будь-який необроблений виняток і логує на рівні
ERROR з текстом "No error handlers are registered, logging exception" —
однаково і для транзитних мережевих збоїв (Telegram Bad Gateway/timeout під
час polling, які PTB сам же й ретраїть), і для справжніх багів у коді
обробників. Це створює шум для будь-якого зовнішнього алертингу на ERROR
(напр. Grafana), бо неможливо відрізнити одне від іншого без читання
трейсбеку.

Тому тут — категоризація: кожен виняток підписується короткою категорією
(`[ПОМИЛКА:<КАТЕГОРІЯ>]`) прямо в тексті логу, а сам виняток лишається на
рівні ERROR з повним трейсбеком (`exc_info`) — нічого не приховуємо, лише
даємо змогу фільтрувати за категорією в Loki/Grafana.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import BadRequest, Forbidden, NetworkError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Порядок важливий: BadRequest — підклас NetworkError (специфічніше — перше).
_CATEGORIES: tuple[tuple[type[Exception], str], ...] = (
    (BadRequest, "TELEGRAM-API"),   # некоректний запит до Telegram API — бага в коді бота
    (Forbidden, "ДОСТУП"),          # юзер заблокував бота / чат недоступний
    (NetworkError, "МЕРЕЖА"),       # охоплює й TimedOut — транзитний збій інфраструктури Telegram
)
_DEFAULT_CATEGORY = "ОБРОБНИК"      # усе інше — найімовірніше, реальна бага в логіці обробника


def categorize_error(error: BaseException) -> str:
    """Визначає коротку категорію винятку для тегу в логах."""
    for error_type, category in _CATEGORIES:
        if isinstance(error, error_type):
            return category
    return _DEFAULT_CATEGORY


async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробник для `Application.add_error_handler` — `update` може бути
    `None` (помилка поза обробкою конкретного update, напр. у job queue)."""
    error = context.error
    category = categorize_error(error) if error is not None else _DEFAULT_CATEGORY
    chat_id = update.effective_chat.id if isinstance(update, Update) and update.effective_chat else None
    logger.error(
        "[ПОМИЛКА:%s] чат=%s: %s", category, chat_id, error, exc_info=error,
    )
