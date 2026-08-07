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
"""

from __future__ import annotations

import functools
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

_Handler = TypeVar("_Handler", bound=Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]])

_BUSY_CHATS_KEY = "busy_chats"


def guarded(callback: _Handler) -> _Handler:
    """Обгортає callback обробника: доки він виконується, чат позначений
    зайнятим — нові натискання (будь-яка кнопка) того самого чату
    ігноруються, замість того щоб стати в чергу на повторне виконання.
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

        if chat_id is not None:
            busy_chats.add(chat_id)
        try:
            await callback(update, context)
        finally:
            busy_chats.discard(chat_id)

    return wrapper  # type: ignore[return-value]
