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
import time
from dataclasses import dataclass
from datetime import date

from telegram.ext import ContextTypes

from findmyjob.bot import texts
from findmyjob.bot.keyboards import build_notification_footer_keyboard
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository
from findmyjob.feeds import VacancyFeedService
from findmyjob.feeds.nda import NDA_CATEGORY
from findmyjob.models import Vacancy
from findmyjob.storage import NotificationSub

logger = logging.getLogger(__name__)

TODAY_ONLY = 0  # days=0 → зріз рівно за поточну дату

# Скільки чатів обслуговуємо одночасно. Усередині чату картки й далі йдуть
# послідовно з паузою — паралелимо саме різні чати.
#
# Вісім, бо картка коштує ~0.55 с (0.25 с sendPhoto + 0.3 с пауза), тобто один
# чат дає 1.8 повідомлення/с, а вісім — 14.5/с при глобальному ліміті Telegram
# ~30/с. Порахували на моделі: 100 підписників по 50 карток — 6 хв замість 46,
# і це вкладається в годину до наступного запуску.
NOTIFY_CONCURRENCY = 8


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

        # NDA-All обслуговує окремий шкедулер (nda_notifier.py, 10:00/14:00/
        # 20:00, дифф проти спільного знімку) — тут її пропускаємо, інакше
        # підписник отримав би сповіщення про неї двічі.
        categories = sorted({
            c for sub in subscriptions.values() for c in sub.categories if c != NDA_CATEGORY
        })
        if not categories:
            # Порожній список тут — це "нема звичайних категорій", а не "нема
            # фільтру": `VacancyFeedService.fetch([])` через `or` впав би на
            # AVAILABLE_CATEGORIES й потягнув би все. Пропускаємо явно.
            logger.info(
                "[СПОВІЩЕННЯ] підписники є, але лише на NDA-All — RSS не чіпаємо"
            )
            return

        logger.info(
            "[СПОВІЩЕННЯ] старт: %d підписник(ів), %d категорій: %s",
            len(subscriptions), len(categories), ", ".join(categories),
        )

        by_category = await self._fetch_today(categories)
        today = date.today().isoformat()
        started = time.monotonic()

        semaphore = asyncio.Semaphore(NOTIFY_CONCURRENCY)
        # Сумарно за прохід — ті самі три цифри, що й у NDA-шкедулері
        # (nda_notifier.py): скільки підійшло під категорії, скільки лишилось
        # після звірки з журналом/прихованими, скільки реально пішло в чат.
        stats = {"matched": 0, "new": 0, "sent": 0}

        async def serve(sub: NotificationSub) -> None:
            async with semaphore:
                batch, matched = self._collect(sub, by_category, context, today)
                sent = await self._deliver(batch, context, today)
                stats["matched"] += matched
                stats["new"] += len(batch.vacancies)
                stats["sent"] += sent
                # Один рядок на КОЖНОГО підписника шкедулера, незалежно від
                # того, чи було що слати — інакше з логів не видно, хто взагалі
                # в проході брав участь, а хто мовчки випав (напр. 0 нових).
                logger.info(
                    "[СПОВІЩЕННЯ] користувач %s: %d підійшло, %d нових, %d розіслано",
                    sub.user_id, matched, len(batch.vacancies), sent,
                )

        results = await asyncio.gather(
            *(serve(sub) for sub in subscriptions.values()), return_exceptions=True
        )
        # Виняток в одного підписника не має ховати решту розсилки з логів.
        for sub, result in zip(subscriptions.values(), results):
            if isinstance(result, BaseException):
                logger.warning(
                    "[СПОВІЩЕННЯ] збій обробки користувача %s: %s", sub.user_id, result
                )

        logger.info(
            "[СПОВІЩЕННЯ] підсумок: %d підійшло під категорії підписників, звірено з "
            "журналом/прихованими — %d нових, фактично розіслано — %d",
            stats["matched"], stats["new"], stats["sent"],
        )
        logger.info("[СПОВІЩЕННЯ] прохід завершено за %.1f с", time.monotonic() - started)

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
    ) -> tuple[UserBatch, int]:
        """Вибирає для користувача те, чого він ще не бачив сьогодні.

        Друге значення — скільки вакансій узагалі підійшло під його категорії
        (до звірки з журналом надісланого й прихованими), для підсумкового
        логу проходу.
        """
        state = self._states.user(context, sub.user_id)
        hidden = state.hidden
        already_sent = self._states.store.sent_today(sub.user_id, today)

        matched: set[str] = set()
        picked: dict[str, dict] = {}
        for category in sub.categories:
            for vacancy in by_category.get(category, ()):
                short_link = vacancy.short_link
                # Одна вакансія може бути в кількох обраних категоріях —
                # set/dict за short_link прибирає такі повтори в межах проходу.
                if short_link in matched:
                    continue
                matched.add(short_link)
                if short_link in hidden or short_link in already_sent:
                    continue
                picked[short_link] = vacancy.to_dict()

        return UserBatch(sub=sub, vacancies=picked), len(matched)

    async def _deliver(
        self, batch: UserBatch, context: ContextTypes.DEFAULT_TYPE, today: str
    ) -> int:
        """Надсилає картки й одразу записує їх у журнал. Повертає скільки пішло."""
        sub = batch.sub
        state = self._states.user(context, sub.user_id)

        if not batch.vacancies:
            # Мовчимо, якщо нема нових — щогодинне "нічого немає" тільки
            # засмічувало чат, повідомлення й кнопка нікому не були потрібні.
            return 0

        # Позначаємо кожну картку одразу після надсилання, а не пачку в кінці:
        # якщо Telegram обірве розсилку посередині, доставлені не прийдуть удруге.
        sent: list[str] = []

        def remember(short_link: str) -> None:
            sent.append(short_link)
            self._states.store.mark_sent(sub.user_id, today, (short_link,))

        try:
            await self._sender.send_all(
                context.bot, sub.chat_id, list(batch.vacancies.values()), state,
                track_seen=False, on_sent=remember,
            )
        except Exception as exc:
            # Заблокований бот або закритий чат не має валити розсилку решті.
            logger.warning(
                "[СПОВІЩЕННЯ] користувач %s: обірвалось на %d з %d — %s",
                sub.user_id, len(sent), len(batch.vacancies), exc,
            )

        if not sent:
            return 0
        # Підсумковий рядок на юзера пише serve() (один раз, з повним набором
        # цифр — підійшло/нових/розіслано) — тут дублювати не треба.
        await self._send_text(context, sub.chat_id, texts.notifications_sent(len(sent)))
        return len(sent)

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
