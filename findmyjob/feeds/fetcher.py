"""Мережеве завантаження RSS-фідів."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence

import feedparser
import requests

from findmyjob.models import Vacancy

from .categories import FeedSource
from .diagnostics import log_dedup, log_feed
from .parsing import EntryParser

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


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

    def fetch_all(self, sources: Sequence[FeedSource]) -> dict[FeedSource, list[Vacancy]]:
        """Завантажує всі джерела паралельно. Без фільтру по даті."""
        if not sources:
            return {}

        results: dict[FeedSource, list[Vacancy]] = {}
        with ThreadPoolExecutor(max_workers=len(sources)) as executor:
            futures = {executor.submit(self.fetch_one, source): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                results[source] = self._drop_repeated_links(source, future.result())
        return results

    def fetch_one(self, source: FeedSource) -> list[Vacancy]:
        """Завантажує й розбирає один фід. Помилка мережі → порожній список."""
        try:
            response = requests.get(source.url, headers=self._headers, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Помилка завантаження %s: %s", source.name, exc)
            return []

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
