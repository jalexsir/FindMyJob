"""Перетворення запису RSS-фіду на доменну модель `Vacancy`."""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime

from findmyjob.feeds.categories import FeedSource, Site
from findmyjob.models import UNKNOWN_DATE, Vacancy, make_vacancy_hash

from .company import extract_company
from .dou_title import parse_dou_title
from .location import UNKNOWN_LOCATION, extract_location

DEFAULT_TITLE = "Без назви"
DATE_FORMAT = "%d.%m.%Y"


class EntryParser:
    """Розбирає один `feedparser`-запис у `Vacancy`.

    DOU-формат заголовка пробуємо для всіх джерел: Djinni теж інколи віддає
    заголовки виду "Посада в Компанія". Те, чого в заголовку не виявилось,
    добираємо з інших полів — але лише способами, доречними для цього сайту.
    """

    def parse(self, entry, source: FeedSource) -> Vacancy:
        published_at = self._parse_date(entry)
        parsed = parse_dou_title(getattr(entry, "title", DEFAULT_TITLE))

        company = parsed.company or extract_company(
            entry, parsed.title, parse_description=source.site is Site.DJINNI
        )
        location = parsed.location or self._location(entry, source)

        return Vacancy(
            source=source.name,
            title=parsed.title,
            link=getattr(entry, "link", ""),
            published=self._format_published(entry, published_at),
            pub_dt=published_at,
            company=company,
            location=location,
            salary=parsed.salary,
            vacancy_hash=make_vacancy_hash(parsed.title, company),
        )

    @staticmethod
    def _location(entry, source: FeedSource) -> str:
        # У DOU локація живе тільки в <title>; парсинг опису там дає хибні збіги.
        if source.site is not Site.DJINNI:
            return UNKNOWN_LOCATION
        return extract_location(entry)

    @staticmethod
    def _parse_date(entry) -> datetime | None:
        raw = getattr(entry, "published", None)
        if not raw:
            return None
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            return None

    @staticmethod
    def _format_published(entry, published_at: datetime | None) -> str:
        if published_at:
            return published_at.strftime(DATE_FORMAT)
        return getattr(entry, "published", "") or UNKNOWN_DATE
