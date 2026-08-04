"""Сценарій сповіщень: підписка на нові вакансії по обраних категоріях.

Категорії сповіщень навмисно окремі від категорій пошуку — шукати руками можна
одне, а отримувати сповіщення про інше. Тому й клавіатура категорій тут та сама,
але з іншими callback_data (`NOTIFY_FLOW`).

Сам розсил робить шкедулер; тут — лише керування підпискою.
"""

from __future__ import annotations

from typing import Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import (
    MAX_SELECTED_CATEGORIES, NOTIFY_FLOW, build_category_keyboard,
    build_notifications_keyboard, build_notify_off_confirm_keyboard,
)

from .base import HandlerGroup


class NotificationHandlers(HandlerGroup):
    """Кнопка меню «Сповіщення», вибір категорій і вимкнення підписки."""

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.setup, pattern=cb.exact(cb.CB_NOTIFY_SETUP)),
            CallbackQueryHandler(self.toggle, pattern=cb.prefixed(cb.CB_NOTIFY_TOGGLE)),
            CallbackQueryHandler(self.change_page, pattern=cb.prefixed(cb.CB_NOTIFY_PAGE)),
            CallbackQueryHandler(self.confirm, pattern=cb.exact(cb.CB_NOTIFY_CONFIRM)),
            CallbackQueryHandler(self.reset, pattern=cb.exact(cb.CB_NOTIFY_RESET)),
            CallbackQueryHandler(self.disable, pattern=cb.exact(cb.CB_NOTIFY_OFF)),
            CallbackQueryHandler(
                self.confirm_disable, pattern=cb.exact(cb.CB_NOTIFY_OFF_YES)
            ),
            CallbackQueryHandler(
                self.cancel_disable, pattern=cb.exact(cb.CB_NOTIFY_OFF_NO)
            ),
        )

    # ── Вхід із меню ─────────────────────────────────────────────────────────

    async def send_prompt(self, chat, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Екран «Сповіщення»: підказка або поточна підписка."""
        subscribed = self.user_state(update, context).notification_categories
        message = await chat.send_message(
            texts.notifications_intro(subscribed),
            parse_mode=ParseMode.HTML,
            reply_markup=build_notifications_keyboard(bool(subscribed)),
        )
        self.session(update, context).track(message.message_id)

    # ── Вибір категорій ──────────────────────────────────────────────────────

    async def setup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Починає вибір із уже підписаних категорій — щоб їх було видно."""
        query = update.callback_query
        await query.answer()

        session = self.session(update, context)
        session.category_page = 0
        session.notify_draft = list(self.user_state(update, context).notification_categories)
        await self._render(query, session.notify_draft, 0)

    async def toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """NDA-All тут — звичайна категорія в межах тієї ж квоти в 5, без
        винятків: на відміну від пошуку, сповіщення про неї можна поєднувати з
        рештою (окремий шкедулер сам зводить її дифф незалежно від інших)."""
        query = update.callback_query
        session = self.session(update, context)
        category = cb.argument(query.data, cb.CB_NOTIFY_TOGGLE)
        draft = session.notify_draft

        if category in draft:
            draft.remove(category)
        elif len(draft) >= MAX_SELECTED_CATEGORIES:
            await query.answer(
                f"Ви можете обрати тільки {MAX_SELECTED_CATEGORIES} категорій одночасно",
                show_alert=True,
            )
            return
        else:
            draft.append(category)

        await query.answer()
        session.notify_draft = draft
        await self._render(query, draft, session.category_page)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Знімає весь поточний вибір (чернетку) одним натисканням."""
        query = update.callback_query
        await query.answer()

        session = self.session(update, context)
        session.notify_draft = []
        await self._render(query, [], session.category_page)

    async def change_page(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        session = self.session(update, context)
        page = int(cb.argument(query.data, cb.CB_NOTIFY_PAGE))
        session.category_page = page
        await self._render(query, session.notify_draft, page)

    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Зберігає підписку — тільки тут вона потрапляє в БД."""
        query = update.callback_query
        session = self.session(update, context)
        draft = session.notify_draft

        if not draft:
            await query.answer(texts.MSG_PICK_AT_LEAST_ONE, show_alert=True)
            return

        await query.answer()
        self.user_state(update, context).subscribe_notifications(
            query.message.chat_id, draft
        )
        session.clear_notify_draft()
        await query.edit_message_text(
            texts.notifications_saved(draft),
            parse_mode=ParseMode.HTML,
            reply_markup=build_notifications_keyboard(subscribed=True),
        )

    # ── Вимкнення ────────────────────────────────────────────────────────────

    async def disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 1: питає підтвердження — разом із підпискою зникають і категорії."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            texts.MSG_NOTIFY_OFF_CONFIRM,
            reply_markup=build_notify_off_confirm_keyboard(),
        )

    async def confirm_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2: прибирає саму підписку, а не лише поточну розсилку."""
        query = update.callback_query
        await query.answer()

        self.user_state(update, context).unsubscribe_notifications()
        self.session(update, context).clear_notify_draft()
        await query.edit_message_text(texts.MSG_NOTIFY_OFF)

    async def cancel_disable(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2 «Ні»: підписка ціла, повертаємо екран із кнопками вибору."""
        query = update.callback_query
        await query.answer()

        subscribed = self.user_state(update, context).notification_categories
        await query.edit_message_text(
            texts.MSG_NOTIFY_OFF_CANCELLED,
            reply_markup=build_notifications_keyboard(bool(subscribed)),
        )

    @staticmethod
    async def _render(query, draft: list[str], page: int) -> None:
        await query.edit_message_text(
            texts.notifications_status(draft),
            parse_mode=ParseMode.HTML,
            reply_markup=build_category_keyboard(draft, page, NOTIFY_FLOW),
        )
