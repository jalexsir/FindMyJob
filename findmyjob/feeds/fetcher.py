"""Мережеве завантаження RSS-фідів."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Sequence

import feedparser
import requests

from findmyjob.models import Vacancy

from .categories import FeedSource
from .diagnostics import log_dedup, log_feed
from .parsing import EntryParser

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
# Стеля потоків. Без неї пул дорівнював би кількості джерел, а вона росте разом
# із кількістю категорій: для всіх 33 це 130 потоків і стільки ж одночасних
# з'єднань до двох хостів — на 1 OCPU і зайва пам'ять, і привід для сайтів
# відповісти 429.
#
# Саме 12, а не менше: максимум для ручного пошуку — 5 категорій, тобто 20
# джерел, і при 12 воркерах це рівно дві хвилі (виміряно: 1.2 с проти 0.6 с без
# стелі, тоді як при 8 було б 1.8 с). Джоба зі всіма категоріями відпрацює за
# ~7 с замість ~1 с, але вона годинна, і там це не має значення.
MAX_PARALLEL_FEEDS = 12

# Джерело, що не відповіло, пробуємо ще двічі. Сім секунд — щоб перечекати
# коротку недоступність, але не тримати людину в очікуванні надто довго:
# у найгіршому випадку одне джерело коштує 3×15 с таймауту + 2×7 с паузи.
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 7

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class FetchEvent:
    """Підсумок однієї спроби завантажити одне джерело.

    `ok=False` разом з `attempt < MAX_ATTEMPTS` означає, що зараз буде повтор.
    """

    source: FeedSource
    attempt: int
    ok: bool

    @property
    def will_retry(self) -> bool:
        return not self.ok and self.attempt < MAX_ATTEMPTS


# Викликається з робочих потоків, тож приймач має бути потокобезпечним.
FetchListener = Callable[[FetchEvent], None]


def worker_count(source_count: int) -> int:
    """Скільки потоків піднімати під `source_count` фідів."""
    return max(1, min(source_count, MAX_PARALLEL_FEEDS))


class FeedFetcher:
    """Завантажує RSS-фіди та повертає вакансії по кожному джерелу.

    Джерела тягнуться ПАРАЛЕЛЬНО: кожен фід — окремий мережевий запит, тож
    послідовно вони могли б коштувати до `timeout` секунд кожен.
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        parser: EntryParser | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._parser = parser or EntryParser()
        self._headers = headers or DEFAULT_HEADERS
        self._local = threading.local()

    @property
    def _session(self) -> requests.Session:
        """HTTP-сесія поточного потоку — заради повторного використання з'єднань.

        Голий `requests.get` робить новий TLS-хендшейк на кожен фід, а їх за
        прохід десятки, і всі до двох хостів. Сесія на потік, а не одна спільна:
        `requests.Session` не гарантує потокобезпечності, а воркерів усе одно
        небагато (`MAX_PARALLEL_FEEDS`), тож з'єднання перевикористовуються.
        """
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self._headers)
            self._local.session = session
        return session

    def fetch_all(
        self, sources: Sequence[FeedSource], on_event: FetchListener | None = None
    ) -> dict[FeedSource, list[Vacancy]]:
        """Завантажує всі джерела паралельно. Без фільтру по даті.

        `on_event` отримує по події на кожну спробу — з робочого потоку.
        """
        if not sources:
            return {}

        results: dict[FeedSource, list[Vacancy]] = {}
        with ThreadPoolExecutor(max_workers=worker_count(len(sources))) as executor:
            futures = {
                executor.submit(self.fetch_one, source, on_event): source
                for source in sources
            }
            for future in as_completed(futures):
                source = futures[future]
                results[source] = self._drop_repeated_links(source, future.result())
        return results

    def fetch_one(
        self, source: FeedSource, on_event: FetchListener | None = None
    ) -> list[Vacancy]:
        """Завантажує й розбирає один фід, з повторами при помилці.

        Вичерпані спроби → порожній список: одне впале джерело не має валити
        всю вибірку, решта показується як є.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._attempt(source, attempt)
            ok = response is not None
            if on_event is not None:
                on_event(FetchEvent(source=source, attempt=attempt, ok=ok))
            if ok:
                return self._parse(source, response)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
        return []

    def _attempt(self, source: FeedSource, attempt: int):
        """Один похід у мережу. Повертає відповідь або None при помилці."""
        try:
            response = self._session.get(source.url, timeout=self._timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            logger.warning(
                "Помилка завантаження %s (спроба %d/%d): %s",
                source.name, attempt, MAX_ATTEMPTS, exc,
            )
            return None

    def _parse(self, source: FeedSource, response) -> list[Vacancy]:
        """Розбирає вже отриману відповідь у список вакансій."""
        log_feed(
            "[%s] HTTP %s | %d байт | %s\n%s",
            source.name, response.status_code, len(response.content), source.url,
            response.content.decode("utf-8", errors="replace"),
        )

        parsed = feedparser.parse(response.content)
        vacancies = [self._parser.parse(entry, source) for entry in parsed.entries]
        log_feed(
            "[%s] Записів у RSS: %d | Завантажено: %d",
            source.name, len(parsed.entries), len(vacancies),
        )
        return vacancies

    @staticmethod
    def _drop_repeated_links(source: FeedSource, vacancies: list[Vacancy]) -> list[Vacancy]:
        """Прибирає повтори В МЕЖАХ одного фіду.

        Тут дублікат — це буквально той самий URL: той самий запис міг прийти в
        RSS двічі. Пари дублікатів навмисно не логуємо — детально їх друкуємо
        лише для фінального злиття.
        """
        seen_links: set[str] = set()
        unique: list[Vacancy] = []
        duplicates = 0

        for vacancy in vacancies:
            if not vacancy.link:
                unique.append(vacancy)
            elif vacancy.link in seen_links:
                duplicates += 1
            else:
                seen_links.add(vacancy.link)
                unique.append(vacancy)

        log_dedup(
            "[FILTER] %s: %d вак. | дублікатів всередині: %d",
            source.name, len(unique), duplicates,
        )
        return unique
