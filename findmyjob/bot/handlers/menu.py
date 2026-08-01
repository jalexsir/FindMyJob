"""Маршрутизація натискань головного меню (Reply Keyboard)."""

from __future__ import annotations

import re
from typing import Awaitable, Callable, Sequence

from telegram import Update
from telegram.ext import BaseHandler, ContextTypes, MessageHandler, filters

from findmyjob.bot import texts
from findmyjob.bot.state import StateRepository

from .base import HandlerGroup
from .favorites import FavoriteHandlers
from .hidden import HiddenHandlers
from .maintenance import MaintenanceHandlers
from .notifications import NotificationHandlers
from .vacancies import VacancyHandlers

MenuAction = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

_MENU_PATTERN = "^({})$".format("|".join(re.escape(text) for text in texts.ALL_BUTTON_TEXTS))


class MenuHandlers(HandlerGroup):
    """Єдина точка входу для кнопок нижнього меню.

    Кнопки — це звичайні текстові повідомлення, тому їх треба зіставляти з
    підписами й розводити по відповідних сценаріях.
    """

    def __init__(
        self,
        states: StateRepository,
        vacancies: VacancyHandlers,
        hidden: HiddenHandlers,
        favorites: FavoriteHandlers,
        notifications: NotificationHandlers,
        maintenance: MaintenanceHandlers,
    ) -> None:
        super().__init__(states)
        self._routes: dict[str, MenuAction] = {
            texts.BTN_VAC_1D: lambda u, c: vacancies.request_from_message(u, c, days=1),
            texts.BTN_VAC_7D: lambda u, c: vacancies.request_from_message(u, c, days=7),
            texts.BTN_VAC_ALL: lambda u, c: vacancies.request_from_message(u, c, days=None),
            texts.BTN_SHOW_HIDDEN: lambda u, c: hidden.send_prompt(u.message.chat, u, c),
            texts.BTN_FAVORITES: lambda u, c: favorites.send_prompt(u.message.chat, u, c),
            texts.BTN_NOTIFICATIONS: lambda u, c: notifications.send_prompt(
                u.message.chat, u, c
            ),
            texts.BTN_CLEAR: lambda u, c: maintenance.send_clear_prompt(
                u, c, chat_id=u.message.chat_id
            ),
            texts.BTN_CLEAR_HIDE: lambda u, c: hidden.clear_all(u.message.chat, u, c),
        }
        self._maintenance = maintenance

    def handlers(self) -> Sequence[BaseHandler]:
        # Порядок важливий: спершу кнопки меню, і лише потім — «усе інше»
        # (обробник у групі спрацьовує лише один, перший, що збігся).
        return (
            MessageHandler(filters.TEXT & filters.Regex(_MENU_PATTERN), self.handle),
            MessageHandler(filters.ALL, self._maintenance.track_user_message),
        )

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self.session(update, context).track(update.message.message_id)
        action = self._routes.get(update.message.text)
        if action is not None:
            await action(update, context)
