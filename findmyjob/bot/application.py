"""Складання застосунку: створення залежностей і реєстрація обробників."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from telegram.ext import Application

from findmyjob.bot.changelog import notify_admin_of_update
from findmyjob.bot.handlers import (
    CategoryHandlers, FavoriteHandlers, HandlerGroup, HiddenHandlers,
    MaintenanceHandlers, MenuHandlers, NdaActionHandlers, NotificationHandlers,
    VacancyHandlers,
)
from findmyjob.bot.nda_notifier import NdaNotificationDispatcher
from findmyjob.bot.notifier import NotificationDispatcher
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import (
    StateRepository, favorites_key, hidden_key, notifications_key,
)
from findmyjob.bot.throttle import guarded
from findmyjob.config import Settings
from findmyjob.feeds import FeedFetcher, VacancyFeedService
from findmyjob.images import VacancyImageRenderer
from findmyjob.storage import VacancyStore

logger = logging.getLogger(__name__)

# Сповіщення: щогодини з 8:00 до 20:00 за київським часом
NOTIFICATIONS_TIMEZONE = "Europe/Kyiv"
NOTIFICATIONS_FROM_HOUR = 8
NOTIFICATIONS_TO_HOUR = 20

# Сповіщення NDA-All: окремий шкедулер, лише тричі на день — не щогодини, бо
# дедуп тут не персональний, а через один спільний знімок (nda_notifier.py).
NDA_NOTIFICATIONS_HOURS = "10,14,20"


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
        notifications = NotificationHandlers(self._states)
        maintenance = MaintenanceHandlers(self._states)
        nda_actions = NdaActionHandlers(self._states, sender, settings.admin_user_id)
        categories = CategoryHandlers(self._states, settings.admin_user_id)
        self._notifier = NotificationDispatcher(self._states, feeds, sender)
        self._nda_notifier = NdaNotificationDispatcher(self._states, sender)

        # Порядок груп = порядок реєстрації обробників. Патерни callback_data не
        # перетинаються, а от текстові обробники меню мають бути останніми.
        self._groups: tuple[HandlerGroup, ...] = (
            categories,
            vacancies,
            hidden,
            favorites,
            notifications,
            nda_actions,
            maintenance,
            MenuHandlers(
                self._states, vacancies, hidden, favorites, notifications, maintenance,
                nda_actions, categories,
            ),
        )

    def build(self) -> Application:
        # concurrent_updates=True — необхідна умова для throttle.guarded():
        # без нього PTB й так обробляє updates строго послідовно, тож
        # per-chat lock ніколи не побачив би "зайнято" (попередній тап уже
        # встиг би повністю завершитись, поки другий дійде до обробки).
        application = (
            Application.builder().token(self._settings.bot_token)
            .concurrent_updates(True).post_init(self._notify_admin_of_update).build()
        )

        self._store.init_db()
        self._backfill_known_users()
        self._preload_state(application)

        for group in self._groups:
            for handler in group.handlers():
                handler.callback = guarded(handler.callback)
                application.add_handler(handler)

        self._schedule_jobs(application)
        return application

    async def _notify_admin_of_update(self, application: Application) -> None:
        """post_init: раз на старт процесу — див. bot/changelog.py."""
        await notify_admin_of_update(
            application.bot, self._store, self._settings.admin_user_id
        )

    def _schedule_jobs(self, application: Application) -> None:
        """Погодинна розсилка сповіщень і нічне прибирання журналу.

        Часовий пояс задається явно: сервер живе в UTC, і без цього «8 ранку»
        перетворилося б на 11:00 за Києвом.
        """
        job_queue = application.job_queue
        if job_queue is None:
            logger.warning(
                "JobQueue недоступна — сповіщення не працюватимуть. "
                "Потрібен пакет python-telegram-bot[job-queue]."
            )
            return

        timezone = ZoneInfo(NOTIFICATIONS_TIMEZONE)
        job_queue.run_custom(
            self._notifier.run,
            job_kwargs={
                "trigger": "cron",
                "hour": f"{NOTIFICATIONS_FROM_HOUR}-{NOTIFICATIONS_TO_HOUR}",
                "minute": 0,
                "timezone": timezone,
            },
            name="notifications",
        )
        job_queue.run_custom(
            self._notifier.purge,
            job_kwargs={"trigger": "cron", "hour": 3, "minute": 0, "timezone": timezone},
            name="notifications-purge",
        )
        job_queue.run_custom(
            self._nda_notifier.run,
            job_kwargs={
                "trigger": "cron", "hour": NDA_NOTIFICATIONS_HOURS, "minute": 0,
                "timezone": timezone,
            },
            name="nda-notifications",
        )
        logger.info(
            "Сповіщення заплановано: щогодини %d:00–%d:00, NDA-All — %s (%s)",
            NOTIFICATIONS_FROM_HOUR, NOTIFICATIONS_TO_HOUR, NDA_NOTIFICATIONS_HOURS,
            NOTIFICATIONS_TIMEZONE,
        )

    def run(self) -> None:
        application = self.build()
        logger.info("Бот запущений. Ctrl+C для зупинки.")
        application.run_polling()

    def _backfill_known_users(self) -> None:
        """Наповнює known_users історичними user_id, якщо вона ще порожня.

        Таблиця з'явилась пізніше за chat_state, тож на вже працюючому боті
        вона інакше показувала б лише тих, хто напише /start ПІСЛЯ деплою
        цієї фічі. no-op, якщо known_users вже непорожня (наступні user_id
        туди додає CategoryHandlers._log_if_new_user() при кожному /start).
        """
        added = self._store.backfill_known_users_from_chat_state()
        if added:
            logger.info(
                "known_users: перенесено %d історичних user_id із chat_state", added
            )

    def _preload_state(self, application: Application) -> None:
        """Підвантажує "Вилучені", "Обране" й підписки з БД у bot_data."""
        all_hidden = self._store.load_all_hidden()
        all_favorites = self._store.load_all_favorites()
        all_subscriptions = self._store.load_all_subscriptions()

        for user_id, records in all_hidden.items():
            application.bot_data[hidden_key(user_id)] = records
        for user_id, records in all_favorites.items():
            application.bot_data[favorites_key(user_id)] = records
        for user_id, subscription in all_subscriptions.items():
            application.bot_data[notifications_key(user_id)] = subscription.categories

        logger.info(
            "Підвантажено з БД: %d hidden-записів, %d favorites-записів, %d підписок",
            sum(len(records) for records in all_hidden.values()),
            sum(len(records) for records in all_favorites.values()),
            len(all_subscriptions),
        )
