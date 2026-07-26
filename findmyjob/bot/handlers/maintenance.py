"""Очищення листування та відстеження повідомлень користувача."""

from __future__ import annotations

from typing import Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import build_category_keyboard

from .base import HandlerGroup


class MaintenanceHandlers(HandlerGroup):
    """Обслуговування чату: видалення історії та облік надісланих повідомлень."""

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.clear_history, pattern=cb.exact(cb.CB_CLEAR)),
        )

    async def clear_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer(texts.MSG_CLEARING)
        await self.clear_chat(
            update, context, chat_id=query.message.chat_id, last_message_id=query.message.message_id
        )

    async def clear_chat(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        chat_id: int,
        last_message_id: int,
    ) -> None:
        """Видаляє все листування й повертає користувача до вибору категорій.

        Telegram не дає списку повідомлень чату, тому йдемо суцільним діапазоном
        від найранішого відстеженого id до поточного: те, що видалити не вдалося
        (чуже або застаріле повідомлення), просто пропускаємо.
        """
        session = self.session(context)
        session.track(last_message_id)

        tracked = session.tracked_message_ids
        first_message_id = min(tracked) if tracked else last_message_id
        start_message_id = session.start_message_id
        session.forget_tracked()

        for message_id in range(first_message_id, last_message_id + 1):
            if message_id == start_message_id:
                continue
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass

        # Скидаємо категорії й повертаємось до їх вибору
        self.user_state(update, context).set_categories([])
        session.category_page = 0

        message = await context.bot.send_message(
            chat_id=chat_id,
            text=texts.categories_status([]),
            parse_mode=ParseMode.HTML,
            reply_markup=build_category_keyboard([]),
        )
        session.track(message.message_id)

    async def track_user_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Запам'ятовує будь-яке повідомлення користувача — щоб потім видалити."""
        if update.message:
            self.session(context).track(update.message.message_id)
