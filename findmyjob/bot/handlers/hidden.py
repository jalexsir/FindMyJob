"""Сценарій "Вилучені з пошуку": приховати, відновити, переглянути, очистити."""

from __future__ import annotations

import asyncio
import html
from typing import Sequence

from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.formatting import format_vacancy
from findmyjob.bot.keyboards import (
    build_clear_hidden_confirm_keyboard, build_reselect_keyboard, build_restore_keyboard,
    build_show_hidden_prompt_keyboard, build_unhide_keyboard, build_vacancy_keyboard,
)
from findmyjob.bot.sending import pace
from findmyjob.bot.state import StateRepository, UserState
from findmyjob.feeds import VacancyFeedService
from findmyjob.images import VacancyImageRenderer
from findmyjob.models import Vacancy

from .base import HandlerGroup, title_from_caption


class HiddenHandlers(HandlerGroup):
    """Керування списком вилучених вакансій."""

    def __init__(
        self,
        states: StateRepository,
        feeds: VacancyFeedService,
        images: VacancyImageRenderer,
    ) -> None:
        super().__init__(states)
        self._feeds = feeds
        self._images = images

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.show_list, pattern=cb.exact(cb.CB_SHOW_HIDDEN)),
            CallbackQueryHandler(self.show_prompt, pattern=cb.exact(cb.CB_SHOW_HIDDEN_PROMPT)),
            CallbackQueryHandler(self.clear, pattern=cb.exact(cb.CB_CLEAR_HIDE)),
            CallbackQueryHandler(self.confirm_clear, pattern=cb.exact(cb.CB_CLEAR_HIDE_YES)),
            CallbackQueryHandler(self.cancel_clear, pattern=cb.exact(cb.CB_CLEAR_HIDE_NO)),
            CallbackQueryHandler(self.hide, pattern=cb.prefixed(cb.CB_HIDE)),
            CallbackQueryHandler(self.unhide, pattern=cb.prefixed(cb.CB_UNHIDE)),
            CallbackQueryHandler(self.restore, pattern=cb.prefixed(cb.CB_RESTORE)),
        )

    # ── Приховати / відновити одну вакансію ──────────────────────────────────

    async def hide(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        short_link = cb.argument(query.data, cb.CB_HIDE)
        state = self.user_state(update, context)
        cached = state.cached_vacancy(short_link)

        if cached:
            title = cached["title"]
            category = cached.get("category")
            entry = {**cached, "categories": [category] if category else []}
        else:
            title = title_from_caption(query.message.caption)
            entry = {"title": title, "categories": []}

        state.hidden[short_link] = entry
        state.save_hidden()

        await query.edit_message_caption(
            caption=f"🙈 <b>Приховано:</b> {html.escape(title)}",
            parse_mode=ParseMode.HTML,
            reply_markup=build_unhide_keyboard(short_link),
        )

    async def unhide(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Повертає щойно приховану вакансію, відновлюючи повну картку."""
        query = update.callback_query
        await query.answer()

        short_link = cb.argument(query.data, cb.CB_UNHIDE)
        state = self.user_state(update, context)
        entry = state.hidden.pop(short_link, {"title": texts.DEFAULT_VACANCY_TITLE})
        state.save_hidden()

        vacancy = await self._restore_vacancy(entry, short_link)
        if vacancy is None:
            title = entry.get("title", texts.DEFAULT_VACANCY_TITLE)
            await query.edit_message_caption(
                caption=(
                    f"👁 <b>{html.escape(title)}</b>\n\n"
                    "Вакансія відновлена. Оновіть список для деталей."
                ),
                parse_mode=ParseMode.HTML,
            )
            return

        await query.message.edit_media(
            media=InputMediaPhoto(
                media=self._images.render(vacancy.title, 0),
                caption=format_vacancy(vacancy),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_vacancy_keyboard(vacancy),
        )

    async def _restore_vacancy(self, entry: dict, short_link: str) -> Vacancy | None:
        """Повні дані вакансії — з кешу, а якщо його немає — з живого фетчу."""
        if "link" in entry:
            return Vacancy.from_dict(entry)

        # Запасний шлях (напр. кеш втрачено після перезапуску бота)
        sources = await asyncio.to_thread(self._feeds.fetch)
        return next(
            (v for source in sources for v in source.vacancies if v.short_link == short_link),
            None,
        )

    async def restore(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Відновлення зі списку вилучених — картка лишається, зникає лише кнопка."""
        query = update.callback_query
        await query.answer(texts.MSG_RESTORED)

        state = self.user_state(update, context)
        state.hidden.pop(cb.argument(query.data, cb.CB_RESTORE), None)
        state.save_hidden()

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    # ── Перегляд списку ──────────────────────────────────────────────────────

    async def show_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await self.send_prompt(query.message.chat, update, context)

    async def show_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await self.send_list(query.message.chat, update, context)

    async def send_prompt(self, chat, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 1: тільки кількість + кнопка підтвердження, без завантаження карток."""
        session = self.session(update, context)
        hidden = self._hidden_for_current_categories(update, context)
        if not hidden:
            message = await chat.send_message(texts.MSG_HIDDEN_EMPTY)
            session.track(message.message_id)
            return

        count = len(hidden)
        message = await chat.send_message(
            f"🙈 <b>У списку вилучених — {count} {texts.vacancies_word(count)}.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_show_hidden_prompt_keyboard(),
        )
        session.track(message.message_id)

    async def send_list(self, chat, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2: власне картки вилучених вакансій (після підтвердження)."""
        session = self.session(update, context)
        hidden = self._hidden_for_current_categories(update, context)
        if not hidden:
            message = await chat.send_message(texts.MSG_HIDDEN_EMPTY)
            session.track(message.message_id)
            return

        header = await chat.send_message(
            f"🙈 <b>Вилучені вакансії ({len(hidden)}):</b>", parse_mode=ParseMode.HTML
        )
        session.track(header.message_id)

        for number, (short_link, entry) in enumerate(hidden.items(), 1):
            if number > 1:
                await pace()
            title = entry.get("title", texts.DEFAULT_VACANCY_TITLE)
            message = await chat.send_photo(
                photo=self._images.render(title, number),
                caption=f"🙈 <b>{html.escape(title)}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=build_restore_keyboard(short_link),
            )
            session.track(message.message_id)

    # ── Очищення ─────────────────────────────────────────────────────────────

    async def clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await self.clear_all(query.message.chat, update, context)

    async def clear_all(self, chat, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 1: кількість вилучених + питання Так/Ні, без самого видалення."""
        count = len(self.user_state(update, context).hidden)
        if not count:
            message = await chat.send_message(texts.MSG_HIDDEN_ALREADY_EMPTY)
            self.session(update, context).track(message.message_id)
            return

        message = await chat.send_message(
            f"🙈 <b>У списку вилучених — {count} {texts.vacancies_word(count)}.</b>"
            "\n\n❓ Очистити список?",
            parse_mode=ParseMode.HTML,
            reply_markup=build_clear_hidden_confirm_keyboard(),
        )
        self.session(update, context).track(message.message_id)

    async def confirm_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2 «Так»: власне очищення — текст той самий, що й раніше."""
        query = update.callback_query
        await query.answer()

        state = self.user_state(update, context)
        count = len(state.hidden)
        state.save_hidden({})
        await query.edit_message_text(f"✅ Список вилучених вакансій очищений ({count})")

    async def cancel_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2 «Ні»: список цілий, пропонуємо переобрати категорії пошуку."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            texts.MSG_RESELECT_PROMPT, reply_markup=build_reselect_keyboard()
        )

    # ── Допоміжне ────────────────────────────────────────────────────────────

    def _hidden_for_current_categories(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> dict[str, dict]:
        state: UserState = self.user_state(update, context)
        return state.hidden_for_categories(state.categories)
