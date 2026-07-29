"""Обробники Telegram, згруповані за сценаріями користувача."""

from .base import HandlerGroup
from .categories import CategoryHandlers
from .favorites import FavoriteHandlers
from .hidden import HiddenHandlers
from .maintenance import MaintenanceHandlers
from .menu import MenuHandlers
from .notifications import NotificationHandlers
from .vacancies import VacancyHandlers

__all__ = [
    "CategoryHandlers",
    "FavoriteHandlers",
    "HandlerGroup",
    "HiddenHandlers",
    "MaintenanceHandlers",
    "MenuHandlers",
    "NotificationHandlers",
    "VacancyHandlers",
]
