"""Сценарій вибору категорій пошуку (`/start` та інлайн-клавіатура)."""

from __future__ import annotations

import logging
from typing import Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, CommandHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import (
    CategoryToggleBlocked, build_intro_continue_keyboard, build_persistent_keyboard,
    toggle_category,
)
from findmyjob.bot.state import StateRepository
from findmyjob.feeds import NDA_CATEGORY

from .base import HandlerGroup, render_category_selection

logger = logging.getLogger(__name__)


class CategoryHandlers(HandlerGroup):
    """Вибір категорій — перший крок будь-якого сценарію."""

    def __init__(self, states: StateRepository, admin_user_id: int | None = None) -> None:
        super().__init__(states)
        self._admin_user_id = admin_user_id

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CommandHandler("start", self.start),
            CallbackQueryHandler(self.toggle, pattern=cb.prefixed(cb.CB_CAT_TOGGLE)),
            CallbackQueryHandler(self.confirm, pattern=cb.exact(cb.CB_CAT_CONFIRM)),
            CallbackQueryHandler(self.reset, pattern=cb.exact(cb.CB_CAT_RESET)),
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
        self._log_if_new_user(update)

        intro = await update.message.reply_text(texts.MSG_INTRO, parse_mode=ParseMode.HTML)
        session.track(intro.message_id)

        footer = await update.message.reply_text(
            texts.MSG_INTRO_FOOTER, reply_markup=build_intro_continue_keyboard()
        )
        session.track(footer.message_id)

    def _log_if_new_user(self, update: Update) -> None:
        """Рахує унікальних користувачів за весь час у БД (`known_users`).

        Логуємо список і тотал лише коли user_id справді новий — повторні
        /start того самого юзера не мають засмічувати лог тим самим списком.
        """
        user = update.effective_user
        if user is None or not self._states.store.register_user(user.id):
            return
        user_ids = self._states.store.load_known_user_ids()
        logger.info(
            "[КОРИСТУВАЧІ] новий користувач %s. Усього унікальних: %d. Список: %s",
            user.id, len(user_ids), ", ".join(str(uid) for uid in user_ids),
        )

    async def continue_intro(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Кнопка «Продовжити» під інтро — показує вибір категорій."""
        query = update.callback_query
        await query.answer()
        await self._render_selection(query, self.user_state(update, context).categories, 0)

    async def toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        state = self.user_state(update, context)
        category = cb.argument(query.data, cb.CB_CAT_TOGGLE)

        try:
            selected = toggle_category(state.categories, category, nda_exclusive=True)
        except CategoryToggleBlocked as exc:
            await query.answer(exc.message, show_alert=True)
            return

        await query.answer()
        state.set_categories(selected)
        await self._render_selection(query, selected, self.session(update, context).category_page)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Знімає весь поточний вибір категорій одним натисканням."""
        query = update.callback_query
        await query.answer()

        state = self.user_state(update, context)
        state.set_categories([])
        await self._render_selection(query, [], self.session(update, context).category_page)

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

        nda_mode = selected == [NDA_CATEGORY]
        is_admin = self._is_admin(update)
        prompt = texts.MSG_PICK_NDA_ACTION if nda_mode else texts.MSG_PICK_PERIOD
        message = await query.message.chat.send_message(
            prompt, reply_markup=build_persistent_keyboard(nda_mode=nda_mode, is_admin=is_admin)
        )
        self.session(update, context).track(message.message_id)

    def _is_admin(self, update: Update) -> bool:
        user = update.effective_user
        return (
            user is not None
            and self._admin_user_id is not None
            and user.id == self._admin_user_id
        )

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
        await render_category_selection(query, selected, page, texts.categories_status)
