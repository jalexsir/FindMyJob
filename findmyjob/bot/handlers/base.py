"""Спільна база для груп обробників."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, ContextTypes

from findmyjob.bot.keyboards import SEARCH_FLOW, CategoryFlow, build_category_keyboard
from findmyjob.bot.state import ChatSession, StateRepository, UserState
from findmyjob.bot.texts import DEFAULT_VACANCY_TITLE

TITLE_CAPTION_MARKER = "Спеціальність:"


class HandlerGroup(ABC):
    """Група обробників, згрупованих за сценарієм користувача."""

    def __init__(self, states: StateRepository) -> None:
        self._states = states

    @abstractmethod
    def handlers(self) -> Sequence[BaseHandler]:
        """Обробники цієї групи в порядку реєстрації."""

    # ── Доступ до стану ──────────────────────────────────────────────────────

    def user_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> UserState:
        user = update.effective_user
        return self._states.user(context, user.id if user else 0)

    def session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> ChatSession:
        return self._states.chat(update, context)


async def render_category_selection(
    query,
    selected: list[str],
    page: int,
    status_text: Callable[[list[str]], str],
    flow: CategoryFlow = SEARCH_FLOW,
) -> None:
    """Перемальовує повідомлення вибору категорій: текст стану + клавіатура.

    Спільне для пошуку й сповіщень — сценарії відрізняються лише функцією
    тексту (`texts.categories_status`/`notifications_status`) і флоу
    клавіатури (`SEARCH_FLOW`/`NOTIFY_FLOW`).
    """
    await query.edit_message_text(
        status_text(selected),
        parse_mode=ParseMode.HTML,
        reply_markup=build_category_keyboard(selected, page, flow),
    )


def title_from_caption(caption: str | None) -> str:
    """Дістає назву вакансії з підпису до картки.

    Запасний шлях на випадок втрати кешу (напр. після перезапуску бота): повних
    даних вакансії вже немає, але сам підпис у чаті лишився.
    """
    for line in (caption or "").split("\n"):
        if TITLE_CAPTION_MARKER in line:
            return line.replace("💼", "").replace(TITLE_CAPTION_MARKER, "").strip()
    return DEFAULT_VACANCY_TITLE
