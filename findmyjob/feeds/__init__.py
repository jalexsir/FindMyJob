"""Підсистема RSS-фідів: категорії, завантаження, парсинг, злиття."""

from .categories import (
    AVAILABLE_CATEGORIES, DJINNI_KEYWORD_MAP, DOU_CATEGORY_MAP,
    FeedSource, Site, Variant, build_sources,
)
from .fetcher import FeedFetcher
from .nda import NDA_CATEGORY, NDA_SOURCE_NAME
from .service import VacancyFeedService

__all__ = [
    "AVAILABLE_CATEGORIES",
    "DJINNI_KEYWORD_MAP",
    "DOU_CATEGORY_MAP",
    "FeedFetcher",
    "FeedSource",
    "NDA_CATEGORY",
    "NDA_SOURCE_NAME",
    "Site",
    "VacancyFeedService",
    "Variant",
    "build_sources",
]
