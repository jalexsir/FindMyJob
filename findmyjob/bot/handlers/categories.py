"""Сценарій вибору категорій пошуку (`/start` та інлайн-клавіатура)."""

from __future__ import annotations

from typing import Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, CommandHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import (
    MAX_SELECTED_CATEGORIES, NdaToggleBlocked, build_category_keyboard,
    build_intro_continue_keyboard, build_persistent_keyboard, resolve_nda_toggle,
)

from .base import HandlerGroup


class CategoryHandlers(HandlerGroup):
    """Вибір категорій — перший крок будь-якого сценарію."""

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CommandHandler("start", self.start),
            CallbackQueryHandler(self.toggle, pattern=cb.prefixed(cb.CB_CAT_TOGGLE)),
            CallbackQueryHandler(self.confirm, pattern=cb.exact(cb.CB_CAT_CONFIRM)),
            CallbackQueryHandler(self.change_page, pattern=cb.prefixed(cb.CB_CAT_PAGE)),
            CallbackQueryHandler(self.noop, pattern=cb.exact(cb.CB_NOOP)),
            CallbackQueryHandler(self.reselect, pattern=cb.exact(cb.CB_RESELECT_CATS)),
            CallbackQueryHandler(self.continue_intro, pattern=cb.exact(cb.CB_INTRO_CONTINUE)),
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        session = self.session(update, context)
        session.start_message_id = update.message.message_id
        session.category_page = 0
        self.user_state(update, context).set_categories([])

        intro = await update.message.reply_text(texts.MSG_INTRO, parse_mode=ParseMode.HTML)
        session.track(intro.message_id)

        footer = await update.message.reply_text(
            texts.MSG_INTRO_FOOTER, reply_markup=build_intro_continue_keyboard()
        )
        session.track(footer.message_id)

    async def continue_intro(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Кнопка «Продовжити» під інтро — показує вибір категорій."""
        query = update.callback_query
        await query.answer()
        await self._render_selection(query, self.user_state(update, context).categories, 0)

    async def toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        state = self.user_state(update, context)
        category = cb.argument(query.data, cb.CB_CAT_TOGGLE)
        selected = state.categories

        try:
            override = resolve_nda_toggle(selected, category)
        except NdaToggleBlocked:
            await query.answer(texts.MSG_NDA_EXCLUSIVE, show_alert=True)
            return

        if override is not None:
            selected = override
        elif category in selected:
            selected.remove(category)
        elif len(selected) >= MAX_SELECTED_CATEGORIES:
            await query.answer(
                f"Ви можете обрати тільки {MAX_SELECTED_CATEGORIES} категорій одночасно",
                show_alert=True,
            )
            return
        else:
            selected.append(category)

        await query.answer()
        state.set_categories(selected)
        await self._render_selection(query, selected, self.session(update, context).category_page)

    async def change_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Перехід між сторінками (◀️/▶️) — вибір категорій не зникає."""
        query = update.callback_query
        await query.answer()

        page = int(cb.argument(query.data, cb.CB_CAT_PAGE))
        self.session(update, context).category_page = page
        await self._render_selection(query, self.user_state(update, context).categories, page)

    async def noop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Кнопка-індикатор сторінки ("2/4") — просто нічого не робить."""
        await update.callback_query.answer()

    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query

        selected = self.user_state(update, context).categories
        if not selected:
            # Відповідаємо на запит саме алертом: повторний answer() вже нічого
            # не показав би, тому звичайного підтвердження вище бути не повинно.
            await query.answer(texts.MSG_PICK_AT_LEAST_ONE, show_alert=True)
            return

        await query.answer()

        await query.edit_message_text(
            texts.categories_confirmed(selected), parse_mode=ParseMode.HTML
        )
        message = await query.message.chat.send_message(
            texts.MSG_PICK_PERIOD, reply_markup=build_persistent_keyboard()
        )
        self.session(update, context).track(message.message_id)

    async def reselect(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Повертає до вибору категорій зі збереженим поточним вибором."""
        query = update.callback_query
        await query.answer()

        session = self.session(update, context)
        # При новому виборі категорій вакансії будуть перезавантажені
        session.clear_pending()
        session.category_page = 0
        await self._render_selection(query, self.user_state(update, context).categories, 0)

    @staticmethod
    async def _render_selection(query, selected: list[str], page: int) -> None:
        await query.edit_message_text(
            texts.categories_status(selected),
            parse_mode=ParseMode.HTML,
            reply_markup=build_category_keyboard(selected, page),
        )
