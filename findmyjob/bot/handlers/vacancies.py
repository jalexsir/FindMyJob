"""Сценарій пошуку та показу вакансій."""

from __future__ import annotations

import asyncio
import queue
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable, Sequence

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import BaseHandler, CallbackQueryHandler, ContextTypes

from findmyjob.bot import callbacks as cb
from findmyjob.bot import texts
from findmyjob.bot.formatting import build_summary
from findmyjob.bot.keyboards import (
    build_category_keyboard, build_confirm_show_keyboard, build_no_vacancies_keyboard,
    build_reselect_keyboard,
)
from findmyjob.bot.progress import SourceProgress
from findmyjob.bot.sending import VacancySender
from findmyjob.bot.state import StateRepository, UserState
from findmyjob.feeds import AVAILABLE_CATEGORIES, NDA_CATEGORY, VacancyFeedService
from findmyjob.feeds.categories import build_sources
from findmyjob.models import Vacancy

from .base import HandlerGroup

ALL_TIME = "all"

# Кожен рядок звіту про джерело надсилається окремим повідомленням.
Reporter = Callable[[str], Awaitable[None]]

# Як часто перевіряти чергу подій від фетчера, поки він працює у фоні.
PROGRESS_POLL_SECONDS = 0.2

# Пауза між видимими змінами рядка стану. Фетч усіх джерел укладається приблизно
# в секунду, тож без неї всі значки перемкнулися б одночасно і людина просто не
# побачила б, що саме відбувалося. Пауза припадає на дедуплікацію, яка все одно
# триває після завантаження, тож майже нічого не додає до загального часу.
STATUS_STEP_SECONDS = 0.4

# Пауза між "Видалення дублікатів…" і редагуванням на "Видалено N дублікатів".
# Сама дедуплікація зазвичай встигає раніше — без цієї паузи заміна тексту
# трапляється практично одразу, і перше повідомлення майже не встигаєш прочитати.
DEDUP_REVEAL_PAUSE_SECONDS = 2.0

# Пауза після "Видалено N дублікатів", перш ніж надсилати зведення. Без неї
# редагування цього повідомлення й наступне зведення прилітають практично
# одночасно — людина не встигає прочитати, скільки саме дублікатів прибрали.
DEDUP_DONE_PAUSE_SECONDS = 1.5


@dataclass(frozen=True)
class VacancySelection:
    """Готова до показу вибірка: текст зведення та скільки в ній вакансій."""

    summary: str
    total: int

    @property
    def is_empty(self) -> bool:
        return self.total == 0


class VacancyHandlers(HandlerGroup):
    """Завантаження вакансій, зведення та показ карток після підтвердження."""

    def __init__(
        self,
        states: StateRepository,
        feeds: VacancyFeedService,
        sender: VacancySender,
        max_per_source: int,
    ) -> None:
        super().__init__(states)
        self._feeds = feeds
        self._sender = sender
        self._max_per_source = max_per_source

    def handlers(self) -> Sequence[BaseHandler]:
        return (
            CallbackQueryHandler(self.show_1d, pattern=cb.exact(cb.CB_VAC_1D)),
            CallbackQueryHandler(self.show_14d, pattern=cb.exact(cb.CB_VAC_14D)),
            CallbackQueryHandler(self.confirm_show, pattern=cb.prefixed(cb.CB_CONFIRM_YES)),
            CallbackQueryHandler(self.decline_show, pattern=cb.exact(cb.CB_CONFIRM_NO)),
        )

    # ── Вхідні точки ─────────────────────────────────────────────────────────

    async def request_from_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, days: int | None
    ) -> None:
        """Запит з кнопки Reply Keyboard: зведення надсилається новим повідомленням."""
        message = update.message
        session = self.session(update, context)

        if not self.user_state(update, context).categories:
            await self._prompt_categories(update, context, message.reply_text)
            return

        waiting = await message.reply_text(texts.MSG_FETCHING)
        session.track(waiting.message_id)

        selection = await self._prepare(
            update, context, days,
            report=self._reporter(session, message.reply_text),
            status=self._status_updater(session, message.reply_text),
            dedup_status=self._status_updater(session, message.reply_text),
        )

        text, keyboard = self._summary_message(selection, days)
        answer = await message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )
        session.track(answer.message_id)

    async def show_1d(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._request_from_callback(update, context, days=1)

    async def show_14d(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._request_from_callback(update, context, days=14)

    async def _request_from_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, days: int | None
    ) -> None:
        """Запит з інлайн-кнопки: зведення замінює саме повідомлення з кнопкою."""
        query = update.callback_query
        await query.answer()
        session = self.session(update, context)
        session.track(query.message.message_id)

        if not self.user_state(update, context).categories:
            session.category_page = 0
            await query.edit_message_text(
                texts.MSG_NO_CATEGORY_SELECTED, reply_markup=build_category_keyboard([])
            )
            return

        await query.edit_message_text(texts.MSG_LOADING)

        selection = await self._prepare(update, context, days)
        text, keyboard = self._summary_message(selection, days)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def _prompt_categories(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, reply
    ) -> None:
        """Жодної категорії не обрано — просимо обрати зі списку замість пошуку.

        Без цього фетч пішов би по всіх категоріях одразу (`categories or None`),
        і користувач отримав би зведення, якого не замовляв.
        """
        session = self.session(update, context)
        session.category_page = 0
        message = await reply(
            texts.MSG_NO_CATEGORY_SELECTED, reply_markup=build_category_keyboard([])
        )
        session.track(message.message_id)

    # ── Підтвердження ────────────────────────────────────────────────────────

    async def confirm_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Крок 2: показує вакансії, вже завантажені на кроці зведення (без ре-фетчу)."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)

        session = self.session(update, context)
        state = self.user_state(update, context)
        pending = session.pending_vacancies

        if not pending:
            message = await query.message.reply_text(
                texts.MSG_NO_VACANCIES, reply_markup=build_no_vacancies_keyboard()
            )
            session.track(message.message_id)
            return

        session.track(*await self._sender.send_all(
            context.bot, query.message.chat_id, pending, state
        ))

        days = self._parse_days(cb.argument(query.data, cb.CB_CONFIRM_YES))
        categories = ", ".join(state.categories or AVAILABLE_CATEGORIES)
        count = len(pending)
        message = await query.message.chat.send_message(
            f"✅ Всі доступні вакансії {texts.period_phrase(days)} "
            f"для категорій ({categories}) доступні для перегляду вище. "
            f"({count} {texts.vacancies_word(count)})",
            reply_markup=build_reselect_keyboard(),
        )
        session.track(message.message_id)

    async def decline_show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        session = self.session(update, context)
        session.clear_pending()
        await query.edit_message_reply_markup(reply_markup=None)
        message = await query.message.reply_text(
            texts.MSG_MAYBE_LATER, reply_markup=build_reselect_keyboard()
        )
        session.track(message.message_id)

    # ── Ядро: завантаження й підготовка вибірки ──────────────────────────────

    async def _prepare(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        days: int | None,
        report: Reporter | None = None,
        status: Reporter | None = None,
        dedup_status: Reporter | None = None,
    ) -> VacancySelection:
        """Фетчить вакансії, прибирає вилучені, впорядковує та кладе в сесію."""
        state = self.user_state(update, context)
        categories = state.categories or None
        sources = await self._fetch(categories, days, report, status, dedup_status)

        hidden_count = self._remove_hidden(sources, state)
        vacancies = sorted(
            (v for source in sources for v in source.vacancies), key=_publication_order
        )
        self.session(update, context).pending_vacancies = [v.to_dict() for v in vacancies]

        return VacancySelection(
            summary=build_summary(sources, days, hidden_count=hidden_count),
            total=len(vacancies),
        )

    def _remove_hidden(self, sources, state: UserState) -> int:
        """Вилучає приховані вакансії з вибірки. Повертає кількість вилучених.

        Рахуємо саме УНІКАЛЬНІ вакансії (за short_link), а не скільки разів вони
        трапилися у фетчі: та сама вакансія може знайтися одразу в кількох
        категоріях (вони між собою не дедуплікуються), тож просте
        "було мінус стало" задвоювало б лічильник.
        """
        hidden = state.hidden
        removed: set[str] = set()
        hidden_changed = False

        for source in sources:
            for vacancy in source.vacancies:
                short_link = vacancy.short_link
                if short_link not in hidden:
                    continue
                removed.add(short_link)
                # Самозцілення: вакансія могла бути прихована з іншої категорії
                # (категорії перетинаються) — тепер точно знаємо, що вона
                # стосується і цієї теж, тож запам'ятовуємо.
                categories = hidden[short_link].setdefault("categories", [])
                if vacancy.category and vacancy.category not in categories:
                    categories.append(vacancy.category)
                    hidden_changed = True

            kept = [v for v in source.vacancies if v.short_link not in hidden]
            # NDA-All — один спільний список без пагінації категорій, тож
            # ліміт "не більше N на джерело" (проти вибуху 1000+ карток при
            # 5 звичайних категоріях) тут не застосовний — показуємо все.
            source.vacancies = kept if source.name == NDA_CATEGORY else kept[:self._max_per_source]

        if hidden_changed:
            state.save_hidden()
        return len(removed)

    # ── Завантаження зі звітом по джерелах ───────────────────────────────────

    async def _fetch(
        self,
        categories: list[str] | None,
        days: int | None,
        report: Reporter | None,
        status: Reporter | None = None,
        dedup_status: Reporter | None = None,
    ):
        """Фетч у робочому потоці; поки він іде — транслюємо його події в чат.

        Фетчер сигналить із робочих потоків, тому події складаються в
        `queue.Queue` (вона потокобезпечна), а надсилає їх уже цей цикл — з
        єдиного потоку, де живе Telegram-клієнт.
        """
        if report is None:
            return await asyncio.to_thread(self._feeds.fetch, days, categories)

        events: queue.Queue = queue.Queue()
        # NDA-All не має RSS-джерел (DOU/Djinni) — виключаємо її з рядка стану,
        # інакше build_sources() згенерував би для неї хибні DOU/Djinni-запити
        # і рядок "Djinni I ⏳ : ..." завис би назавжди (події для них ніколи
        # не прийдуть, бо VacancyFeedService.fetch() її окремо не фетчить).
        rss_categories = [c for c in (categories or AVAILABLE_CATEGORIES) if c != NDA_CATEGORY]
        progress = SourceProgress(build_sources(rss_categories))
        task = asyncio.create_task(
            asyncio.to_thread(self._feeds.fetch, days, categories, events.put)
        )

        shown = progress.status_line()
        if status is not None and shown:
            await status(shown)

        async def refresh() -> None:
            """Показує рядок, якщо він змінився, і витримує паузу після зміни."""
            nonlocal shown
            line = progress.status_line()
            if line == shown or status is None or not line:
                return
            shown = line
            await status(line)
            await asyncio.sleep(STATUS_STEP_SECONDS)

        # Джерела всі відзвітували, але дедуплікація (Етап 2) ще йде у фоновому
        # потоці — task стає done() лише після неї. Повідомляємо про це один
        # раз, з тією ж паузою, що й переходи рядка стану.
        dedup_announced = False

        async def announce_dedup() -> None:
            nonlocal dedup_announced
            if dedup_announced or dedup_status is None or not progress.is_complete:
                return
            dedup_announced = True
            await asyncio.sleep(STATUS_STEP_SECONDS)
            await dedup_status(texts.MSG_DEDUP_IN_PROGRESS)

        while not task.done() or not events.empty():
            for line in progress.slow_reports():
                await report(line)
            try:
                event = events.get_nowait()
            except queue.Empty:
                await asyncio.sleep(PROGRESS_POLL_SECONDS)
                continue
            for line in progress.consume(event):
                await report(line)
            await refresh()
            await announce_dedup()

        await refresh()
        await announce_dedup()
        sources = await task

        if dedup_status is not None:
            await asyncio.sleep(DEDUP_REVEAL_PAUSE_SECONDS)
            total_duplicates = sum(source.duplicates for source in sources)
            await dedup_status(texts.dedup_done(total_duplicates))
            await asyncio.sleep(DEDUP_DONE_PAUSE_SECONDS)

        return sources

    def _reporter(self, session, reply) -> Reporter:
        """Надсилає рядок звіту окремим повідомленням і бере його на облік."""
        async def send(line: str) -> None:
            message = await reply(line)
            session.track(message.message_id)
        return send

    def _status_updater(self, session, reply) -> Reporter:
        """Тримає ОДНЕ повідомлення зі станом джерел і редагує його на місці.

        Перший виклик надсилає повідомлення, наступні — правлять його. Текст без
        змін не надсилаємо: Telegram відповідає на таке помилкою.
        """
        holder: dict[str, object] = {}

        async def update(line: str) -> None:
            if holder.get("text") == line:
                return
            holder["text"] = line
            message = holder.get("message")
            if message is None:
                message = await reply(line)
                holder["message"] = message
                session.track(message.message_id)
                return
            try:
                await message.edit_text(line)
            except Exception:
                # Правка могла не пройти (повідомлення видалили) — не привід
                # валити пошук: далі просто не оновлюємо рядок.
                pass

        return update

    # ── Допоміжне ────────────────────────────────────────────────────────────

    @staticmethod
    def _summary_message(selection: VacancySelection, days: int | None):
        """Текст і клавіатура для повідомлення зі зведенням."""
        if selection.is_empty:
            return selection.summary, build_no_vacancies_keyboard()
        return (
            f"{selection.summary}\n\n{texts.MSG_SHOW_VACANCIES}",
            build_confirm_show_keyboard(days),
        )

    @staticmethod
    def _parse_days(raw: str) -> int | None:
        return None if raw == ALL_TIME else int(raw)


def _publication_order(vacancy: Vacancy) -> tuple[bool, date]:
    """Сортування за датою: найстаріші зверху, найсвіжіші — останнім повідомленням.

    Вакансії без визначеної дати йдуть у кінець, а не помилково на початок.
    """
    published_at = vacancy.publication_date()
    return published_at is None, published_at or date.min
