"""Налаштування застосунку — єдине місце, де читається оточення.

Решта модулів отримує `Settings` через конструктор, а не звертається до
`os.getenv` напряму: так їх можна створити з довільною конфігурацією в тестах.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "bot_storage.db"


@dataclass(frozen=True)
class Settings:
    """Конфігурація бота."""

    bot_token: str
    db_path: Path = DEFAULT_DB_PATH

    # Мережа й кешування RSS
    request_timeout: int = 15
    cache_ttl_seconds: int = 120

    # Скільки вакансій одного джерела максимум показуємо за раз
    max_vacancies_per_source: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        """Читає .env та змінні оточення."""
        load_dotenv()
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не знайдено. Створи .env з BOT_TOKEN=...")
        return cls(bot_token=token)
