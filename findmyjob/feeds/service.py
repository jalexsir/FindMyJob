"""Сервіс вакансій: завантаження, двоетапне злиття та TTL-кеш.

ВАЖЛИВО щодо порядку операцій (щоб "видалено N дублікатів" ніколи не
перевищувало кількість показаних вакансій):

1. Кешується/фетчиться лише ЕТАП 1 (внутрішньосайтове злиття за посиланням) —
   БЕЗ фільтру по даті, тож усі часові вікна (1 день / 7 днів / весь час)
   ділять один і той самий знімок фіду.
2. Списки етапу 1 обрізаються по даті — вже для конкретного запиту.
3. І ЛИШЕ ПОТІМ — ЕТАП 2 (фінальне злиття DOU + Djinni за назвою/компанією) на
   вже дато-обрізаних списках. Дублікати рахуються саме тут, тож їх кількість
   завжди узгоджена з тим, що реально показано.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from findmyjob.models import MergedSource, Vacancy

from .categories import AVAILABLE_CATEGORIES, Site, Variant, build_sources
from .dedup import merge_by_link, merge_by_title_and_company
from .diagnostics import log_dedup, log_vacancy_list
from .fetcher import FeedFetcher, FetchEvent, FetchListener
from .nda import NDA_CATEGORY, get_nda_source

DEFAULT_CACHE_TTL_SECONDS = 120


@dataclass
class CategoryFeeds:
    """Результат етапу 1 для однієї категорії — по одному списку на сайт."""

    dou: list[Vacancy] = field(default_factory=list)
    djinni: list[Vacancy] = field(default_factory=list)


Stage1 = dict[str, CategoryFeeds]


class VacancyFeedService:
    """Публічний вхід у підсистему фідів."""

    def __init__(
        self,
        fetcher: FeedFetcher | None = None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self._fetcher = fetcher or FeedFetcher()
        self._cache = _Stage1Cache(cache_ttl_seconds)

    def fetch(
        self,
        days: int | None = None,
        categories: list[str] | None = None,
        on_event: FetchListener | None = None,
    ) -> list[MergedSource]:
        """Повертає по одному злитому списку вакансій на кожну категорію.

        `days=None` — без фільтру по даті. Блокуючий виклик (мережа): з
        асинхронного коду запускати через `asyncio.to_thread`.

        `on_event` отримує подію на кожну спробу завантаження — це те, з чого
        бот показує стан по джерелах. При влученні в кеш мережі не було, тож
        події синтезуються як успішні: дані все одно щойно звідти й приїхали.

        `NDA_CATEGORY` — окрема гілка: спільний для всіх список з nda.in.ua
        (без RSS, без дати, без злиття з DOU/Djinni), тож у стандартний
        конвеєр стадій 1/2 і в `_Stage1Cache` вона не потрапляє взагалі —
        інакше псувала б ключ кешу для звичайних RSS-категорій.
        """
        active_categories = categories or AVAILABLE_CATEGORIES
        rss_categories = [c for c in active_categories if c != NDA_CATEGORY]
        cache_key = tuple(sorted(rss_categories))

        stage1 = self._cache.get(cache_key)
        from_cache = stage1 is not None
        if stage1 is None:
            stage1 = self._build_stage1(rss_categories, on_event)
            self._cache.put(cache_key, stage1)
        elif on_event is not None:
            for source in build_sources(rss_categories):
                on_event(FetchEvent(source=source, attempt=1, ok=True))

        log_dedup(
            "ЕТАП 2 (%s, %s)",
            "з кешу" if from_cache else "живий фетч",
            f"останні {days} д." if days is not None else "весь час",
        )

        cutoff = _date_cutoff(days)
        return [
            get_nda_source() if category == NDA_CATEGORY else
            self._merge_category(category, stage1.get(category, CategoryFeeds()), cutoff)
            for category in active_categories
        ]

    # ── Етапи ────────────────────────────────────────────────────────────────

    def _build_stage1(
        self, categories: list[str], on_event: FetchListener | None = None
    ) -> Stage1:
        """Етап 0 (сирі дані) + Етап 1 (внутрішньосайтове злиття за посиланням)."""
        raw = self._fetcher.fetch_all(build_sources(categories), on_event)

        log_dedup("ЕТАП 0: Сирі дані")
        for source, vacancies in raw.items():
            log_vacancy_list(source.name, vacancies)

        by_source = {
            (source.category, source.site, source.variant): vacancies
            for source, vacancies in raw.items()
        }

        def pick(category: str, site: Site, variant: Variant) -> list[Vacancy]:
            return by_source.get((category, site, variant), [])

        stage1: Stage1 = {}
        for category in categories:
            log_dedup("ЗЛИТТЯ (Етап 1): %s", category)
            feeds = CategoryFeeds(
                djinni=merge_by_link(
                    f"Temp Djinni {category}",
                    pick(category, Site.DJINNI, Variant.DEFTECH),
                    pick(category, Site.DJINNI, Variant.RESERVATION),
                ).vacancies,
                dou=merge_by_link(
                    f"Temp DOU {category}",
                    pick(category, Site.DOU, Variant.DEFTECH),
                    pick(category, Site.DOU, Variant.RESERVATION),
                ).vacancies,
            )

            for vacancy in (*feeds.djinni, *feeds.dou):
                vacancy.category = category

            log_vacancy_list(f"Temp Djinni {category}", feeds.djinni)
            log_vacancy_list(f"Temp DOU {category}", feeds.dou)
            stage1[category] = feeds

        return stage1

    @staticmethod
    def _merge_category(category: str, feeds: CategoryFeeds, cutoff: date | None) -> MergedSource:
        """Етап 2: фільтр по даті + фінальне злиття DOU (primary) з Djinni."""
        dou = [v for v in feeds.dou if _passes_date(v, cutoff)]
        djinni = [v for v in feeds.djinni if _passes_date(v, cutoff)]

        merged = merge_by_title_and_company(f"Final {category}", dou, djinni)
        log_vacancy_list(f"Final {category}", merged.vacancies)

        return MergedSource(
            name=f"{category} (з бронюванням)",
            vacancies=merged.vacancies,
            duplicates=merged.duplicates,
        )


class _Stage1Cache:
    """Потокобезпечний TTL-кеш результатів етапу 1.

    Клік по кнопці меню без нього означав би повний ре-фетч усіх RSS-джерел з
    нуля; повторні запити з тим самим набором категорій віддаються миттєво.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, ...], tuple[float, Stage1]] = {}

    def get(self, key: tuple[str, ...]) -> Stage1 | None:
        with self._lock:
            self._evict_expired()
            entry = self._entries.get(key)
            return None if entry is None else entry[1]

    def put(self, key: tuple[str, ...], value: Stage1) -> None:
        with self._lock:
            self._evict_expired()
            self._entries[key] = (time.monotonic(), value)

    def _evict_expired(self) -> None:
        """Прибирає протухле. Викликається під локом.

        Без цього кеш тільки ріс би: кожен новий набір категорій — це окремий
        ключ на кілька сотень кілобайт, і протухлі записи трималися б у пам'яті
        до перезапуску процесу. Наборів рівно стільки, скільки різних комбінацій
        категорій обрали користувачі, тож саме собою це не стабілізується.
        """
        now = time.monotonic()
        expired = [
            key for key, (stored_at, _) in self._entries.items()
            if (now - stored_at) >= self._ttl
        ]
        for key in expired:
            del self._entries[key]


def _date_cutoff(days: int | None) -> date | None:
    return None if days is None else date.today() - timedelta(days=days)


def _passes_date(vacancy: Vacancy, cutoff: date | None) -> bool:
    """Вакансії з невідомою датою публікації лишаємо — краще показати зайве."""
    if cutoff is None:
        return True
    published_at = vacancy.publication_date()
    if published_at is None:
        return True
    return published_at >= cutoff
