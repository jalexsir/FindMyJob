"""Складання застосунку: створення залежностей і реєстрація обробників."""

from __future__ import annotations

import logging

from telegram.ext import Application

from findmyjob.bot.handlers import (
    CategoryHandlers, FavoriteHandlers, HandlerGroup, HiddenHandlers,
    MaintenanceHandlers, MenuHandlers, VacancyHandlers,
)
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository, favorites_key, hidden_key
from findmyjob.config import Settings
from findmyjob.feeds import FeedFetcher, VacancyFeedService
from findmyjob.images import VacancyImageRenderer
from findmyjob.storage import VacancyStore

logger = logging.getLogger(__name__)


class BotApplication:
    """Кореневий об'єкт застосунку — тут і тільки тут збираються всі залежності."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._store = VacancyStore(settings.db_path)
        self._states = StateRepository(self._store)

        feeds = VacancyFeedService(
            fetcher=FeedFetcher(timeout=settings.request_timeout),
            cache_ttl_seconds=settings.cache_ttl_seconds,
        )
        images = VacancyImageRenderer()
        sender = VacancySender(images)

        vacancies = VacancyHandlers(
            self._states, feeds, sender, settings.max_vacancies_per_source
        )
        hidden = HiddenHandlers(self._states, feeds, images)
        favorites = FavoriteHandlers(self._states, feeds, sender)
        maintenance = MaintenanceHandlers(self._states)

        # Порядок груп = порядок реєстрації обробників. Патерни callback_data не
        # перетинаються, а от текстові обробники меню мають бути останніми.
        self._groups: tuple[HandlerGroup, ...] = (
            CategoryHandlers(self._states),
            vacancies,
            hidden,
            favorites,
            maintenance,
            MenuHandlers(self._states, vacancies, hidden, favorites, maintenance),
        )

    def build(self) -> Application:
        application = Application.builder().token(self._settings.bot_token).build()

        self._store.init_db()
        self._preload_state(application)

        for group in self._groups:
            for handler in group.handlers():
                application.add_handler(handler)

        return application

    def run(self) -> None:
        application = self.build()
        logger.info("Бот запущений. Ctrl+C для зупинки.")
        application.run_polling()

    def _preload_state(self, application: Application) -> None:
        """Підвантажує "Вилучені" та "Обране" з БД у bot_data одним заходом."""
        all_hidden = self._store.load_all_hidden()
        all_favorites = self._store.load_all_favorites()

        for user_id, records in all_hidden.items():
            application.bot_data[hidden_key(user_id)] = records
        for user_id, records in all_favorites.items():
            application.bot_data[favorites_key(user_id)] = records

        logger.info(
            "Підвантажено з БД: %d hidden-записів, %d favorites-записів",
            sum(len(records) for records in all_hidden.values()),
            sum(len(records) for records in all_favorites.values()),
        )
