"""Погодинна розсилка нових вакансій підписникам.

Один прохід обслуговує ВСІХ підписників: категорії всіх користувачів
об'єднуються, фіди тягнуться один раз, і вже завантажене розкладається по
людях. Інакше десять підписників на Python означали б десять однакових наборів
RSS-запитів.

Що вважається «новим»: вакансія за сьогодні, якої ще не було в журналі
надісланого за цей день. Журнал накопичувальний і живе в SQLite — бот
перезапускається на кожному деплої, і список у пам'яті означав би повтори.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from telegram.ext import ContextTypes

from findmyjob.bot import texts
from findmyjob.bot.keyboards import build_notification_footer_keyboard
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository
from findmyjob.feeds import VacancyFeedService
from findmyjob.models import Vacancy
from findmyjob.storage import NotificationSub

logger = logging.getLogger(__name__)

TODAY_ONLY = 0  # days=0 → зріз рівно за поточну дату


@dataclass(frozen=True)
class UserBatch:
    """Що саме надсилаємо одному користувачу цього разу.

    Ключі — `short_link`: вони ж ідуть у журнал надісланого. Тримати їх поруч
    треба тому, що `Vacancy.to_dict()` їх не містить — це обчислювана
    властивість, і відновлювати її з даних довелося б повторно.
    """

    sub: NotificationSub
    vacancies: dict[str, dict]


class NotificationDispatcher:
    """Джоби шкедулера: погодинна розсилка та нічне прибирання журналу."""

    def __init__(
        self,
        states: StateRepository,
        feeds: VacancyFeedService,
        sender: VacancySender,
    ) -> None:
        self._states = states
        self._feeds = feeds
        self._sender = sender

    # ── Погодинна джоба ──────────────────────────────────────────────────────

    async def run(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        store = self._states.store
        subscriptions = store.load_all_subscriptions()
        if not subscriptions:
            logger.info("[СПОВІЩЕННЯ] підписників немає — фіди не чіпаємо")
            return

        categories = sorted({c for sub in subscriptions.values() for c in sub.categories})
        logger.info(
            "[СПОВІЩЕННЯ] старт: %d підписник(ів), %d категорій: %s",
            len(subscriptions), len(categories), ", ".join(categories),
        )

        by_category = await self._fetch_today(categories)
        today = date.today().isoformat()

        for sub in subscriptions.values():
            batch = self._collect(sub, by_category, context, today)
            await self._deliver(batch, context, today)

    async def _fetch_today(self, categories: list[str]) -> dict[str, list[Vacancy]]:
        """Один похід у RSS на всіх підписників. Повертає {категорія: вакансії}."""
        sources = await asyncio.to_thread(self._feeds.fetch, TODAY_ONLY, categories)
        result = dict(zip(categories, (source.vacancies for source in sources)))

        total = sum(len(v) for v in result.values())
        undated = sum(
            1 for vacancies in result.values()
            for v in vacancies if v.publication_date() is None
        )
        # Вакансії без дати публікації навмисно проходять фільтр "за сьогодні"
        # (краще показати зайве). Лічильник — щоб бачити, чи вони взагалі
        # трапляються, і чи не варто їх зрештою відкидати.
        logger.info(
            "[СПОВІЩЕННЯ] завантажено %d вакансій за сьогодні, з них без дати: %d",
            total, undated,
        )
        return result

    def _collect(
        self,
        sub: NotificationSub,
        by_category: dict[str, list[Vacancy]],
        context: ContextTypes.DEFAULT_TYPE,
        today: str,
    ) -> UserBatch:
        """Вибирає для користувача те, чого він ще не бачив сьогодні."""
        state = self._states.user(context, sub.user_id)
        hidden = state.hidden
        already_sent = self._states.store.sent_today(sub.user_id, today)

        picked: dict[str, dict] = {}
        for category in sub.categories:
            for vacancy in by_category.get(category, ()):
                short_link = vacancy.short_link
                # Одна вакансія може бути в кількох обраних категоріях — dict за
                # short_link прибирає такі повтори в межах цього ж проходу.
                if short_link in picked or short_link in hidden or short_link in already_sent:
                    continue
                picked[short_link] = vacancy.to_dict()

        return UserBatch(sub=sub, vacancies=picked)

    async def _deliver(
        self, batch: UserBatch, context: ContextTypes.DEFAULT_TYPE, today: str
    ) -> None:
        """Надсилає картки й одразу записує їх у журнал."""
        sub = batch.sub
        state = self._states.user(context, sub.user_id)

        if not batch.vacancies:
            await self._send_text(context, sub.chat_id, texts.MSG_NOTIFY_NOTHING_NEW)
            return

        try:
            await self._sender.send_all(
                context.bot, sub.chat_id, list(batch.vacancies.values()), state,
                track_seen=False,
            )
        except Exception as exc:
            # Заблокований бот або закритий чат не має валити розсилку решті.
            logger.warning(
                "[СПОВІЩЕННЯ] не вдалося надіслати користувачу %s: %s", sub.user_id, exc
            )
            return

        self._states.store.mark_sent(sub.user_id, today, batch.vacancies)
        count = len(batch.vacancies)
        logger.info("[СПОВІЩЕННЯ] користувач %s: надіслано %d", sub.user_id, count)
        await self._send_text(context, sub.chat_id, texts.notifications_sent(count))

    @staticmethod
    async def _send_text(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        """Підсумок із кнопкою вимкнення — щоб вихід був під рукою, а не в меню."""
        try:
            await context.bot.send_message(
                chat_id, text, reply_markup=build_notification_footer_keyboard()
            )
        except Exception as exc:
            logger.warning("[СПОВІЩЕННЯ] чат %s недоступний: %s", chat_id, exc)

    # ── Нічне прибирання ─────────────────────────────────────────────────────

    async def purge(self, _: ContextTypes.DEFAULT_TYPE) -> None:
        removed = self._states.store.purge_sent_before(date.today().isoformat())
        logger.info("[СПОВІЩЕННЯ] журнал за попередні дні очищено: %d рядків", removed)
