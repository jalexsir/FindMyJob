"""Очищення листування та відстеження повідомлень користувача."""

from __future__ import annotations

from typing import Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import (
    build_category_keyboard, build_clear_confirm_keyboard, build_reselect_keyboard,
)

from .base import HandlerGroup


class MaintenanceHandlers(HandlerGroup):
    """Обслуговування чату: видалення історії та облік надісланих повідомлень."""

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.clear_history, pattern=cb.exact(cb.CB_CLEAR)),
            CallbackQueryHandler(self.confirm_clear, pattern=cb.exact(cb.CB_CLEAR_YES)),
            CallbackQueryHandler(self.cancel_clear, pattern=cb.exact(cb.CB_CLEAR_NO)),
        )

    async def clear_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await self.send_clear_prompt(update, context, chat_id=query.message.chat_id)

    async def send_clear_prompt(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, chat_id: int
    ) -> None:
        """Питає підтвердження, перш ніж стирати все листування."""
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=texts.MSG_CLEAR_CONFIRM,
            reply_markup=build_clear_confirm_keyboard(),
        )
        self.session(update, context).track(message.message_id)

    async def confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """«Так»: власне очищення — межа діапазону це саме повідомлення підтвердження."""
        query = update.callback_query
        await query.answer(texts.MSG_CLEARING)
        await self.clear_chat(
            update, context, chat_id=query.message.chat_id, last_message_id=query.message.message_id
        )

    async def cancel_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """«Ні»: листування ціле, пропонуємо переобрати категорії пошуку."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            texts.MSG_RESELECT_PROMPT, reply_markup=build_reselect_keyboard()
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

        Нижня межа діапазону береться з `chat_state` у БД, тому очищення працює
        і після перезапуску бота — на відміну від попередньої версії, де перелік
        повідомлень жив лише в пам'яті процесу й губився при рестарті.
        """
        session = self.session(update, context)
        session.track(last_message_id)

        first_tracked = session.first_tracked_message_id
        first_message_id = last_message_id if first_tracked is None else first_tracked
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

        intro = await context.bot.send_message(
            chat_id=chat_id, text=texts.MSG_INTRO, parse_mode=ParseMode.HTML
        )
        session.track(intro.message_id)

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
            self.session(update, context).track(update.message.message_id)
