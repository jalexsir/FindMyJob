"""Підсистема RSS-фідів: категорії, завантаження, парсинг, злиття."""

from .categories import (
    AVAILABLE_CATEGORIES, DJINNI_KEYWORD_MAP, DOU_CATEGORY_MAP,
    FeedSource, Site, Variant, build_sources,
)
from .fetcher import FeedFetcher
from .service import VacancyFeedService

__all__ = [
    "AVAILABLE_CATEGORIES",
    "DJINNI_KEYWORD_MAP",
    "DOU_CATEGORY_MAP",
    "FeedFetcher",
    "FeedSource",
    "Site",
    "VacancyFeedService",
    "Variant",
    "build_sources",
]
