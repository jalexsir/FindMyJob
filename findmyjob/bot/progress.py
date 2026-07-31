"""Стан завантаження джерел одним рядком, який оновлюється на місці.

Джерел рівно чотири — DOU і Djinni × Deftech і бронювання, — але кожне з них
опитується окремо для КОЖНОЇ обраної категорії. Тобто при двох категоріях буде
вісім запитів, а людині все одно треба показати чотири значки.

Тому події від фетчера зводяться за парою (сайт, різновид): значок джерела
стає підсумковим тоді, коли по ньому відзвітували всі категорії, і успішним —
лише якщо жодна з них не впала.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from findmyjob.feeds.categories import Site, Variant
from findmyjob.feeds.fetcher import MAX_ATTEMPTS, FetchEvent

# Через скільки мовчання джерела попереджаємо, що воно гальмує. П'ять секунд —
# приблизно середина восьмисекундного таймауту: звичайний фетч укладається в
# ~1.2 с, тож повідомлення прилетить лише коли справді щось не так.
SLOW_AFTER_SECONDS = 5.0

ICON_PENDING = "⏳"
ICON_RETRY = "🔄"
ICON_OK = "✅"
ICON_FAIL = "❌"

# Римська цифра замість назви різновиду: рядок має влізти в один екран.
_NUMERAL = {Variant.DEFTECH: "I", Variant.RESERVATION: "II"}

# Порядок у рядку фіксований — щоб значки не стрибали між оновленнями.
DISPLAY_ORDER = [
    (site, variant)
    for site in (Site.DJINNI, Site.DOU)
    for variant in (Variant.DEFTECH, Variant.RESERVATION)
]

Key = tuple[Site, Variant]


@dataclass
class _SourceState:
    """Скільки категорій цього джерела ще в дорозі та що з ними сталося."""

    pending: int
    failed: bool = False
    retrying: bool = False
    announced_attempts: set[int] = field(default_factory=set)
    announced_slow: bool = False

    @property
    def icon(self) -> str:
        if self.pending:
            # `failed` теж дає 🔄: одна категорія вже впала, інша ще в дорозі —
            # інакше значок миготів би між 🔄 і ⏳ між категоріями.
            return ICON_RETRY if (self.retrying or self.failed) else ICON_PENDING
        return ICON_FAIL if self.failed else ICON_OK


class SourceProgress:
    """Перетворює потік подій фетчера на рядок стану та окремі попередження.

    Нічого не надсилає сама — лише повертає готові тексти, а надсилання лишає
    викликачу: так її можна перевірити без Telegram.
    """

    def __init__(self, sources, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._states: dict[Key, _SourceState] = {}
        for source in sources:
            key = self._key_of(source)
            state = self._states.setdefault(key, _SourceState(pending=0))
            state.pending += 1

    @property
    def total_sources(self) -> int:
        return len(self._states)

    def status_line(self) -> str:
        """Рядок виду `Djinni I ⏳ | Djinni II ✅ | DOU I 🔄 | DOU II ❌`."""
        return " | ".join(
            f"{self._name(key)} {self._states[key].icon}"
            for key in DISPLAY_ORDER
            if key in self._states
        )

    def consume(self, event: FetchEvent) -> list[str]:
        """Оновлює стан джерела. Повертає окремі повідомлення (про повтори)."""
        state = self._states.get(self._key_of(event.source))
        if state is None:
            return []

        if event.will_retry:
            state.retrying = True
            # Про повтор кажемо один раз на джерело, а не на кожну категорію:
            # інакше при п'яти категоріях прилетить п'ять однакових рядків.
            if event.attempt in state.announced_attempts:
                return []
            state.announced_attempts.add(event.attempt)
            return [
                f"⚠️ {self._label(self._key_of(event.source))} — "
                f"Помилка, спроба {event.attempt + 1}/{MAX_ATTEMPTS}"
            ]

        state.retrying = False
        if not event.ok:
            state.failed = True
        state.pending -= 1
        return []

    def slow_reports(self) -> list[str]:
        """Рядки про джерела, які мовчать довше за `SLOW_AFTER_SECONDS`.

        Кажемо про кожне джерело один раз: людині досить знати, що затримка є і
        через кого саме, а не отримувати нагадування щосекунди.
        """
        if self._clock() - self._started < SLOW_AFTER_SECONDS:
            return []

        messages = []
        for key in DISPLAY_ORDER:
            state = self._states.get(key)
            if state and state.pending and not state.announced_slow:
                state.announced_slow = True
                messages.append(
                    f"⏳ Почекай ще трошки, маємо затримку від джерела {self._name(key)}"
                )
        return messages

    @staticmethod
    def _key_of(source) -> Key:
        return source.site, source.variant

    @staticmethod
    def _name(key: Key) -> str:
        site, variant = key
        return f"{site.value} {_NUMERAL[variant]}"

    @classmethod
    def _label(cls, key: Key) -> str:
        return f"{cls._name(key)} джерело"
