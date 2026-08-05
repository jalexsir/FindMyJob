"""Тричі на день (10:00, 14:00, 20:00) розсилка нових вакансій NDA-All.

На відміну від `notifier.py` (звичайні категорії): дедуп тут не персональний
(накопичувальний журнал на юзера), а через ОДИН спільний знімок
(`nda_notification_baseline`) — фетчиться свіжий стан, різниця з попереднім
знімком іде повідомленням усім підписаним на NDA-All, а сам знімок після
цього ПОВНІСТЮ замінюється щойно завантаженим (не накопичується).

Сплячий режим: якщо на NDA-All не підписаний жоден користувач, прохід
завершується одразу — жодного запиту до nda.in.ua й жодного оновлення
знімку. Знімок лишається "замороженим" до першої ж підписки; наступний
прохід після неї звірить його з поточним станом сайту як завжди.
"""

from __future__ import annotations

import asyncio
import logging
import time

from telegram.ext import ContextTypes

from findmyjob.bot import texts
from findmyjob.bot.keyboards import build_notification_footer_keyboard
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository
from findmyjob.feeds.nda import NDA_CATEGORY, get_nda_source
from findmyjob.storage import NotificationSub

logger = logging.getLogger(__name__)

# Той самий принцип паралелизму, що й у звичайній розсилці (notifier.py) —
# кілька чатів одночасно, картки всередині чату йдуть послідовно.
NOTIFY_CONCURRENCY = 8


class NdaNotificationDispatcher:
    """Джоба шкедулера: 10:00 / 14:00 / 20:00, окремо від погодинної розсилки."""

    def __init__(self, states: StateRepository, sender: VacancySender) -> None:
        self._states = states
        self._sender = sender

    async def run(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        store = self._states.store
        subscribers = [
            sub for sub in store.load_all_subscriptions().values()
            if NDA_CATEGORY in sub.categories
        ]

        if not subscribers:
            # Сплячий режим: нема кому слати — не чіпаємо nda.in.ua й не
            # оновлюємо знімок. Знімок лишається "замороженим" до першої ж
            # підписки; наступний прохід після цього звірить його з поточним
            # станом сайту, як і мало б бути.
            logger.info("[NDA-СПОВІЩЕННЯ] підписників немає — сайт не чіпаємо")
            return

        logger.info("[NDA-СПОВІЩЕННЯ] старт: %d підписник(ів)", len(subscribers))
        started = time.monotonic()

        # to_thread: get_nda_source() робить синхронний requests.get() і не
        # має блокувати event loop бота на час запиту.
        current = (await asyncio.to_thread(get_nda_source)).vacancies
        baseline_links = store.load_nda_notification_baseline_links()
        diff = [v for v in current if v.link not in baseline_links]

        # Знімок оновлюється завжди, коли підписники Є (навіть якщо дифф
        # порожній) — лише повна відсутність підписників вимикає шкедулер.
        store.save_nda_notification_baseline([(v.link, v.title) for v in current])

        logger.info(
            "[NDA-СПОВІЩЕННЯ] завантажено %d вакансій, звірено з попереднім "
            "знімком (%d), нових — %d",
            len(current), len(baseline_links), len(diff),
        )

        if not diff:
            # Один рядок на КОЖНОГО підписника навіть тут — інакше з логів не
            # видно, хто взагалі в шкедулері бере участь, коли діффу немає.
            for sub in subscribers:
                logger.info(
                    "[NDA-СПОВІЩЕННЯ] user-%s: %d категорій, 0 нових, 0 розіслано",
                    sub.user_id, len(sub.categories),
                )
            logger.info(
                "[NDA-СПОВІЩЕННЯ] прохід завершено за %.1f с — нових немає, нікому не шлемо",
                time.monotonic() - started,
            )
            return

        payload = [v.to_dict() for v in diff]
        semaphore = asyncio.Semaphore(NOTIFY_CONCURRENCY)

        async def serve(sub: NotificationSub) -> None:
            async with semaphore:
                await self._deliver(sub, payload, context)

        results = await asyncio.gather(
            *(serve(sub) for sub in subscribers), return_exceptions=True
        )
        for sub, result in zip(subscribers, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "[NDA-СПОВІЩЕННЯ] збій обробки user-%s: %s", sub.user_id, result
                )

        logger.info("[NDA-СПОВІЩЕННЯ] прохід завершено за %.1f с", time.monotonic() - started)

    async def _deliver(
        self, sub: NotificationSub, payload: list[dict], context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        state = self._states.user(context, sub.user_id)
        sent = 0

        def remember(_: str) -> None:
            nonlocal sent
            sent += 1

        try:
            await self._sender.send_all(
                context.bot, sub.chat_id, payload, state, track_seen=False, on_sent=remember,
            )
        except Exception as exc:
            # Заблокований бот або закритий чат не має валити розсилку решті.
            logger.warning(
                "[NDA-СПОВІЩЕННЯ] user-%s: обірвалось на %d з %d — %s",
                sub.user_id, sent, len(payload), exc,
            )

        # Один рядок на КОЖНОГО підписника, незалежно від результату —
        # інакше з логів не видно, хто взагалі брав участь у проході.
        logger.info(
            "[NDA-СПОВІЩЕННЯ] user-%s: %d категорій, %d нових, %d розіслано",
            sub.user_id, len(sub.categories), len(payload), sent,
        )

        if not sent:
            return
        try:
            await context.bot.send_message(
                sub.chat_id,
                texts.notifications_sent(sent),
                reply_markup=build_notification_footer_keyboard(),
            )
        except Exception as exc:
            logger.warning("[NDA-СПОВІЩЕННЯ] чат %s недоступний: %s", sub.chat_id, exc)
