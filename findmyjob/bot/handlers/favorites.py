"""Сценарій "Обране": додати, прибрати, переглянути список."""

from __future__ import annotations

import asyncio
from typing import Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.keyboards import build_favorites_confirm_keyboard, replace_button
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository, UserState
from findmyjob.feeds import VacancyFeedService

from .base import HandlerGroup, title_from_caption


class FavoriteHandlers(HandlerGroup):
    """Керування списком обраних вакансій."""

    def __init__(
        self,
        states: StateRepository,
        feeds: VacancyFeedService,
        sender: VacancySender,
    ) -> None:
        super().__init__(states)
        self._feeds = feeds
        self._sender = sender

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.add, pattern=cb.prefixed(cb.CB_FAVORITE)),
            CallbackQueryHandler(self.remove, pattern=cb.prefixed(cb.CB_UNFAVORITE)),
            CallbackQueryHandler(self.delete_from_list, pattern=cb.prefixed(cb.CB_FAV_DELETE)),
            CallbackQueryHandler(self.show_all, pattern=cb.exact(cb.CB_FAVS_YES)),
            CallbackQueryHandler(self.decline_show, pattern=cb.exact(cb.CB_FAVS_NO)),
        )

    # ── Додавання / видалення ────────────────────────────────────────────────

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer(texts.MSG_ADDED_TO_FAVORITES)

        short_link = cb.argument(query.data, cb.CB_FAVORITE)
        state = self.user_state(update, context)
        cached = state.cached_vacancy(short_link)

        # Запасний шлях (напр. кеш втрачено після перезапуску бота) —
        # беремо тільки назву з підпису
        state.favorites[short_link] = cached or {
            "title": title_from_caption(query.message.caption),
            "short_link": short_link,
        }
        state.save_favorites()

        await self._swap_button(
            query,
            target=cb.payload(cb.CB_FAVORITE, short_link),
            replacement=InlineKeyboardButton(
                texts.BTN_IN_FAVORITES,
                callback_data=cb.payload(cb.CB_UNFAVORITE, short_link),
            ),
        )

    async def remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer(texts.MSG_REMOVED_FROM_FAVORITES)

        short_link = cb.argument(query.data, cb.CB_UNFAVORITE)
        self._forget(update, context, short_link)

        await self._swap_button(
            query,
            target=cb.payload(cb.CB_UNFAVORITE, short_link),
            replacement=InlineKeyboardButton(
                texts.BTN_ADD_FAVORITE_ALT,
                callback_data=cb.payload(cb.CB_FAVORITE, short_link),
            ),
        )

    async def delete_from_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Видалення при перегляді списку Обраних — лишаємо тільки "Відкрити"."""
        query = update.callback_query
        await query.answer(texts.MSG_REMOVED_FROM_FAVORITES_LIST)

        self._forget(update, context, cb.argument(query.data, cb.CB_FAV_DELETE))

        try:
            open_button = query.message.reply_markup.inline_keyboard[0][0]
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[open_button]])
            )
        except Exception:
            await query.edit_message_reply_markup(reply_markup=None)

    # ── Перегляд списку ──────────────────────────────────────────────────────

    async def send_prompt(self, chat, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показує кількість обраних і питає Так/Ні."""
        session = self.session(context)
        count = len(self.user_state(update, context).favorites)
        if not count:
            message = await chat.send_message(texts.MSG_FAVORITES_EMPTY)
            session.track(message.message_id)
            return

        message = await chat.send_message(
            f"⭐ <b>У списку Обраних є {count} {texts.vacancies_word(count)}.</b>"
            "\n\n❓ Показати їх?",
            parse_mode=ParseMode.HTML,
            reply_markup=build_favorites_confirm_keyboard(),
        )
        session.track(message.message_id)

    async def show_all(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

        session = self.session(context)
        state = self.user_state(update, context)
        if not state.favorites:
            message = await query.message.reply_text(texts.MSG_FAVORITES_EMPTY_SHORT)
            session.track(message.message_id)
            return

        vacancies = await self._collect(state)
        if not vacancies:
            message = await query.message.reply_text(texts.MSG_FAVORITES_GONE)
            session.track(message.message_id)
            return

        session.track(*await self._sender.send_all(
            query.message.chat, vacancies, state,
            force_favorite=True, from_favorites=True,
        ))
        message = await query.message.chat.send_message(
            f"✅ Показано {len(vacancies)} {texts.favorite_vacancies_phrase(len(vacancies))}."
        )
        session.track(message.message_id)

    async def decline_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        message = await query.message.reply_text(texts.MSG_TICK_TOCK)
        self.session(context).track(message.message_id)

    async def _collect(self, state: UserState) -> list[dict]:
        """Повні дані обраних вакансій.

        Зазвичай вони вже закешовані з моменту додавання; живий фетч потрібен
        лише для старих записів без кешу (напр. після перезапуску бота).
        """
        vacancies: list[dict] = []
        missing: set[str] = set()

        for short_link, entry in state.favorites.items():
            if "link" in entry:
                vacancies.append(entry)
            else:
                missing.add(short_link)

        if missing:
            sources = await asyncio.to_thread(self._feeds.fetch, None)
            vacancies.extend(
                vacancy.to_dict()
                for source in sources
                for vacancy in source.vacancies
                if vacancy.short_link in missing
            )
        return vacancies

    # ── Допоміжне ────────────────────────────────────────────────────────────

    def _forget(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, short_link: str
    ) -> None:
        state = self.user_state(update, context)
        state.favorites.pop(short_link, None)
        state.save_favorites()

    @staticmethod
    async def _swap_button(query, target: str, replacement: InlineKeyboardButton) -> None:
        """Перемальовує клавіатуру зі зміненою кнопкою обраного.

        Повідомлення могло бути видалене або застаріти — тоді просто лишаємо
        клавіатуру як є, це не привід ламати обробник.
        """
        try:
            await query.edit_message_reply_markup(
                reply_markup=replace_button(query.message.reply_markup, target, replacement)
            )
        except Exception:
            pass
