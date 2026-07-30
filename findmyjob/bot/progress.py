"""Звіт про стан завантаження джерел для чату.

Джерел рівно чотири — DOU і Djinni × Deftech і бронювання, — але кожне з них
опитується окремо для КОЖНОЇ обраної категорії. Тобто при двох категоріях буде
вісім запитів, а людині все одно треба показати чотири рядки.

Тому тут події від фетчера зводяться за парою (сайт, різновид): рядок
з'являється тоді, коли по цьому джерелу відзвітували всі категорії, а вердикт
успішний лише якщо жодна з них не впала.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from findmyjob.feeds.fetcher import MAX_ATTEMPTS, FetchEvent

# Через скільки мовчання джерела попереджаємо, що воно гальмує. П'ять секунд —
# приблизно середина восьмисекундного таймауту: звичайний фетч укладається в
# ~1.2 с, тож повідомлення прилетить лише коли справді щось не так.
SLOW_AFTER_SECONDS = 5.0


@dataclass
class _SourceState:
    """Скільки категорій цього джерела ще в дорозі та чи були провали."""

    pending: int
    failed: bool = False
    announced_attempts: set[int] = field(default_factory=set)
    announced_slow: bool = False


class SourceProgress:
    """Перетворює потік подій фетчера на рядки для чату.

    Не надсилає нічого сама — лише повертає готові тексти, а надсилання лишає
    викликачу: так її можна перевірити без Telegram.
    """

    def __init__(self, sources, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._states: dict[tuple[str, str], _SourceState] = {}
        self._order: list[tuple[str, str]] = []
        for source in sources:
            key = self._key_of(source)
            if key not in self._states:
                self._states[key] = _SourceState(pending=0)
                self._order.append(key)
            self._states[key].pending += 1

    @property
    def total_sources(self) -> int:
        return len(self._order)

    def consume(self, event: FetchEvent) -> list[str]:
        """Повертає рядки, які треба надіслати після цієї події (може бути порожньо)."""
        key = self._key_of(event.source)
        state = self._states.get(key)
        if state is None:
            return []

        messages: list[str] = []

        if event.will_retry:
            # Про повтор кажемо один раз на джерело, а не на кожну категорію:
            # інакше при п'яти категоріях прилетить п'ять однакових рядків.
            if event.attempt not in state.announced_attempts:
                state.announced_attempts.add(event.attempt)
                messages.append(
                    f"⚠️ {self._label(key)} — Помилка, спроба {event.attempt + 1}/{MAX_ATTEMPTS}"
                )
            return messages

        if not event.ok:
            state.failed = True

        state.pending -= 1
        if state.pending == 0:
            messages.append(
                f"{'❌' if state.failed else '✅'} {self._label(key)} — "
                f"{'Помилка' if state.failed else 'Успішно'}"
            )
        return messages

    def slow_reports(self) -> list[str]:
        """Рядки про джерела, які мовчать довше за `SLOW_AFTER_SECONDS`.

        Кажемо про кожне джерело один раз: людині досить знати, що затримка є і
        через кого саме, а не отримувати нагадування щосекунди.
        """
        if self._clock() - self._started < SLOW_AFTER_SECONDS:
            return []

        messages = []
        for key in self._order:
            state = self._states[key]
            if state.pending and not state.announced_slow:
                state.announced_slow = True
                messages.append(
                    f"⏳ Почекай ще трошки, маємо затримку від джерела {self._name(key)}"
                )
        return messages

    @staticmethod
    def _key_of(source) -> tuple[str, str]:
        return source.site.value, source.variant.value

    @staticmethod
    def _name(key: tuple[str, str]) -> str:
        site, variant = key
        return f"{site} {variant}"

    @classmethod
    def _label(cls, key: tuple[str, str]) -> str:
        return f"{cls._name(key)} джерело"
