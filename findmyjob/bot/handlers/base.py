"""Спільна база для груп обробників."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from telegram import Update
from telegram.ext import BaseHandler, ContextTypes

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


def title_from_caption(caption: str | None) -> str:
    """Дістає назву вакансії з підпису до картки.

    Запасний шлях на випадок втрати кешу (напр. після перезапуску бота): повних
    даних вакансії вже немає, але сам підпис у чаті лишився.
    """
    for line in (caption or "").split("\n"):
        if TITLE_CAPTION_MARKER in line:
            return line.replace("💼", "").replace(TITLE_CAPTION_MARKER, "").strip()
    return DEFAULT_VACANCY_TITLE
