"""Захист від дублювання дій під час швидких повторних натискань.

Один чат — одна дія одночасно. Без цього кожен зайвий тап, що прийшов, поки
триває обробка попереднього (RSS-фетч, надсилання карток тощо), просто стає
в чергу — і все одно повністю відпрацьовує (свій фетч, свої дублікати
карток), тільки вже ПІСЛЯ завершення першого. Порівняння часу тут не
рятує: за замовчуванням PTB обробляє updates строго послідовно
(`concurrent_updates=False`), тож "щойно був інший тап" завжди означає
"поки виконувався попередній" і не дає відрізнити повторний клік від
звичайної наступної дії користувача.

Тому: `application.py` вмикає `concurrent_updates=True` при білді
`Application` (лише тоді блокування одного чату не тримає весь бот — інші
чати обробляються паралельно), а тут — банальний per-chat lock у
`bot_data`, що вмикається на старті обробки й знімається по завершенню.

Саму лише тривалість обробки як "час зайнятості" недостатньо: швидкі дії
(наприклад, "Обране" — просто читання зі стану, без мережевих запитів)
завершуються за долі секунди, тож наступний тап людини, що поспішно тисне
кнопку кілька разів поспіль, майже завжди приходить уже ПІСЛЯ завершення
попереднього — лок ніколи не встигає їх "спіймати", і кожен тап відпрацьовує
окремо. Тому лок тримається не менше `_MIN_BUSY_SECONDS` від старту
обробки, навіть якщо сам callback завершився миттєво.

Це навмисно КОРОТКИЙ поріг (частки секунди): він має проковтнути лише
випадкові швидкі повтори (перш ніж людина встигла побачити відповідь), а не
блокувати свідоме повторне натискання тієї самої кнопки вже ПІСЛЯ того, як
відповідь на попереднє прийшла — це вже не дублікат, а нова дія користувача.

Окремо від цього — `guard_against_abuse()`: захист від зловживання (шквалу
запитів), а не від випадкового дубль-кліку. Рахує натискання будь-яких кнопок
за ковзне вікно `_ABUSE_WINDOW_SECONDS`; якщо їх більше за
`_ABUSE_MAX_REQUESTS` — чат переходить у режим тайм-ауту на
`_ABUSE_LOCKOUT_SECONDS`: усі кнопки ігноруються. Попередження показується як
нативний Telegram alert (`answer(show_alert=True)`) для inline-кнопок — так
само, як MSG_NDA_EXCLUSIVE. Через це відлік тайм-ауту стартує одразу в
момент показу: Telegram не повідомляє бота, коли користувач закриває такий
alert (кнопка "Гаразд" — суто локальна дія клієнта), тож підтвердження від
користувача тут не чекаємо. Для кнопок нижнього меню (reply keyboard, не
callback query — alert для них технічно неможливий) — звичайне повідомлення
без кнопки. На відміну від `guarded()`, тут не важливо, чи дії різні (Обране,
потім Приховані, потім...) — рахується сам факт частоти запитів від чату.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import deque
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

from findmyjob.bot import texts

_Handler = TypeVar("_Handler", bound=Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]])

_BUSY_CHATS_KEY = "busy_chats"

# Мінімальна тривалість блокування чату від старту обробки — щоб проковтнути
# серію нетерплячих повторних тапів навіть по миттєвих діях. Коротко навмисно:
# довше за типовий інтервал між двома випадковими тапами (~150-400мс), але
# помітно коротше за час, потрібний людині, щоб побачити відповідь і свідомо
# натиснути ще раз.
_MIN_BUSY_SECONDS = 0.6


def guarded(callback: _Handler) -> _Handler:
    """Обгортає callback обробника: доки він виконується (і ще щонайменше
    `_MIN_BUSY_SECONDS` після старту), чат позначений зайнятим — нові
    натискання (будь-яка кнопка) того самого чату ігноруються, замість того
    щоб стати в чергу на повторне виконання.
    """

    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        busy_chats: set[int] = context.bot_data.setdefault(_BUSY_CHATS_KEY, set())

        if chat_id is not None and chat_id in busy_chats:
            if update.callback_query is not None:
                # Інакше кнопка в клієнті лишається "у завантаженні" до таймауту.
                await update.callback_query.answer()
            return

        if chat_id is None:
            await callback(update, context)
            return

        busy_chats.add(chat_id)
        started = time.monotonic()
        try:
            await callback(update, context)
        finally:
            remaining = _MIN_BUSY_SECONDS - (time.monotonic() - started)
            if remaining > 0:
                await asyncio.sleep(remaining)
            busy_chats.discard(chat_id)

    return wrapper  # type: ignore[return-value]


# ── Захист від абузу (шквалу запитів) ──────────────────────────────────────

_ABUSE_LOG_KEY = "abuse_request_log"        # dict[chat_id, deque[float]]
_ABUSE_LOCKOUT_KEY = "abuse_lockout_until"  # dict[chat_id, float]

_ABUSE_WINDOW_SECONDS = 10.0
_ABUSE_MAX_REQUESTS = 10
_ABUSE_LOCKOUT_SECONDS = 10.0


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return chat.id if chat else None


async def _answer_if_callback(update: Update) -> None:
    if update.callback_query is not None:
        # Інакше кнопка в клієнті лишається "у завантаженні" до таймауту.
        await update.callback_query.answer()


def guard_against_abuse(callback: _Handler) -> _Handler:
    """Обгортає callback обробника: рахує натискання будь-яких кнопок чату за
    ковзне вікно `_ABUSE_WINDOW_SECONDS`. Якщо їх більше за
    `_ABUSE_MAX_REQUESTS` — показує попередження й переводить чат у режим
    тайм-ауту на `_ABUSE_LOCKOUT_SECONDS`: усі кнопки ігноруються.
    """

    @functools.wraps(callback)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = _chat_id(update)
        if chat_id is None:
            await callback(update, context)
            return

        lockout_until: dict[int, float] = context.bot_data.setdefault(_ABUSE_LOCKOUT_KEY, {})
        until = lockout_until.get(chat_id)
        if until is not None:
            if time.monotonic() < until:
                await _answer_if_callback(update)
                return
            del lockout_until[chat_id]

        log: dict[int, deque[float]] = context.bot_data.setdefault(_ABUSE_LOG_KEY, {})
        timestamps = log.setdefault(chat_id, deque())
        now = time.monotonic()
        timestamps.append(now)
        while timestamps and now - timestamps[0] > _ABUSE_WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) > _ABUSE_MAX_REQUESTS:
            timestamps.clear()
            lockout_until[chat_id] = now + _ABUSE_LOCKOUT_SECONDS
            if update.callback_query is not None:
                await update.callback_query.answer(texts.MSG_ABUSE_DETECTED, show_alert=True)
            else:
                await context.bot.send_message(chat_id=chat_id, text=texts.MSG_ABUSE_DETECTED)
            return

        await callback(update, context)

    return wrapper  # type: ignore[return-value]
