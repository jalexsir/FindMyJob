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
    # Вісім, а не п'ятнадцять: джерело пробується тричі з паузою 7 с, тож у
    # найгіршому випадку мертвий фід коштує 3×8 + 2×7 = 38 с очікування замість 59.
    request_timeout: int = 8
    cache_ttl_seconds: int = 120

    # Скільки вакансій одного джерела максимум показуємо за раз
    max_vacancies_per_source: int = 100

    # Telegram user_id власника бота — лише йому показується кнопка
    # "Оновити еталон" для NDA-All. None, якщо не задано (кнопка не показується).
    admin_user_id: int | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """Читає .env та змінні оточення."""
        load_dotenv()
        token = (os.getenv("BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_TOKEN не знайдено. Створи .env з BOT_TOKEN=...")

        admin_raw = (os.getenv("ADMIN_USER_ID") or "").strip()
        admin_user_id = int(admin_raw) if admin_raw.isdigit() else None

        return cls(bot_token=token, admin_user_id=admin_user_id)
