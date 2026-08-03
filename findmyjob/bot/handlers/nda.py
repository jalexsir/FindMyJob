"""Сценарій дій NDA-All: показати нові/усі вакансії, оновити еталон (адмін).

Ці дії заміняють кнопки періоду ("за 1 день" тощо) на нижньому меню, коли
обрана лише категорія NDA-All — у неї немає дати публікації, тож поняття
періоду до неї незастосовне. "Еталон" — знімок списку в SQLite, з яким
звіряють "Показати нові"; оновлюється лише вручну, ніколи автоматично.
"""

from __future__ import annotations

import asyncio
from typing import Sequence

from telegram import Update
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import (
    build_category_keyboard, build_nda_all_confirm_keyboard,
    build_nda_baseline_confirm_keyboard, build_nda_new_confirm_keyboard,
    build_reselect_keyboard,
)
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository
from findmyjob.feeds.nda import NDA_CATEGORY, get_nda_source, refresh_nda_source

from .base import HandlerGroup


class NdaActionHandlers(HandlerGroup):
    """Кнопки нижнього меню, доступні лише коли обрана саме NDA-All."""

    def __init__(
        self, states: StateRepository, sender: VacancySender, admin_user_id: int | None
    ) -> None:
        super().__init__(states)
        self._sender = sender
        self._admin_user_id = admin_user_id

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.confirm_new, pattern=cb.exact(cb.CB_NDA_SHOW_NEW_YES)),
            CallbackQueryHandler(self.decline, pattern=cb.exact(cb.CB_NDA_SHOW_NEW_NO)),
            CallbackQueryHandler(self.confirm_all, pattern=cb.exact(cb.CB_NDA_SHOW_ALL_YES)),
            CallbackQueryHandler(self.decline, pattern=cb.exact(cb.CB_NDA_SHOW_ALL_NO)),
            CallbackQueryHandler(
                self.confirm_baseline_update, pattern=cb.exact(cb.CB_NDA_BASELINE_YES)
            ),
            CallbackQueryHandler(
                self.cancel_baseline_update, pattern=cb.exact(cb.CB_NDA_BASELINE_NO)
            ),
        )

    def is_admin(self, update: Update) -> bool:
        user = update.effective_user
        return (
            user is not None
            and self._admin_user_id is not None
            and user.id == self._admin_user_id
        )

    # ── Показати нові (дифф проти еталону) ───────────────────────────────────

    async def show_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if self.user_state(update, context).categories != [NDA_CATEGORY]:
            await self._prompt_reselect(update, context)
            return

        session = self.session(update, context)
        waiting = await message.reply_text(texts.MSG_FETCHING)
        session.track(waiting.message_id)

        # to_thread: get_nda_source() робить синхронний requests.get() — без
        # цього мережевий запит блокував би весь event loop (усіх користувачів
        # одночасно), а не лише цей чат. Найпомітніше на холодному кеші —
        # тобто саме на першому виборі NDA-All.
        current = (await asyncio.to_thread(get_nda_source)).vacancies
        baseline_links = self._states.store.load_nda_baseline_links()
        diff = [v for v in current if v.link not in baseline_links]

        session.pending_vacancies = [v.to_dict() for v in diff]
        reply = await message.reply_text(
            texts.nda_new_summary(len(diff)),
            reply_markup=(
                build_nda_new_confirm_keyboard() if diff else build_reselect_keyboard()
            ),
        )
        session.track(reply.message_id)

    async def confirm_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_pending(update, context)

    # ── Показати всі (без порівняння) ────────────────────────────────────────

    async def show_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if self.user_state(update, context).categories != [NDA_CATEGORY]:
            await self._prompt_reselect(update, context)
            return

        session = self.session(update, context)
        waiting = await message.reply_text(texts.MSG_FETCHING)
        session.track(waiting.message_id)

        current = (await asyncio.to_thread(get_nda_source)).vacancies
        session.pending_vacancies = [v.to_dict() for v in current]
        reply = await message.reply_text(
            texts.nda_all_summary(len(current)),
            reply_markup=(
                build_nda_all_confirm_keyboard() if current else build_reselect_keyboard()
            ),
        )
        session.track(reply.message_id)

    async def confirm_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_pending(update, context)

    async def decline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        session = self.session(update, context)
        session.clear_pending()
        message = await query.message.reply_text(
            texts.MSG_MAYBE_LATER, reply_markup=build_reselect_keyboard()
        )
        session.track(message.message_id)

    async def _send_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

        session = self.session(update, context)
        state = self.user_state(update, context)
        pending = session.pending_vacancies
        if pending:
            session.track(*await self._sender.send_all(
                context.bot, query.message.chat_id, pending, state
            ))
        session.clear_pending()

    # ── Оновити еталон (лише адмін) ──────────────────────────────────────────

    async def prompt_update_baseline(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self.is_admin(update):
            return
        session = self.session(update, context)
        message = await update.message.reply_text(
            texts.MSG_NDA_UPDATE_BASELINE_CONFIRM,
            reply_markup=build_nda_baseline_confirm_keyboard(),
        )
        session.track(message.message_id)

    async def confirm_baseline_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not self.is_admin(update):
            await query.answer()
            return
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

        fresh = (await asyncio.to_thread(refresh_nda_source)).vacancies
        self._states.store.save_nda_baseline([(v.link, v.title) for v in fresh])

        session = self.session(update, context)
        message = await query.message.reply_text(texts.nda_baseline_updated(len(fresh)))
        session.track(message.message_id)

    async def cancel_baseline_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        session = self.session(update, context)
        message = await query.message.reply_text(texts.MSG_NDA_BASELINE_CANCELLED)
        session.track(message.message_id)

    # ── Допоміжне ────────────────────────────────────────────────────────────

    async def _prompt_reselect(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Категорії скинуто (напр. рестарт бота) — кнопка лишилась на клавіатурі,
        але діяти нема з чим, поки NDA-All не обрано знову."""
        session = self.session(update, context)
        session.category_page = 0
        message = await update.message.reply_text(
            texts.MSG_NO_CATEGORY_SELECTED, reply_markup=build_category_keyboard([])
        )
        session.track(message.message_id)
