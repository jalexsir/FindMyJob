"""Точка входу: `python bot.py`.

Уся логіка живе в пакеті `findmyjob` — тут лише налаштування логування та запуск.
"""

import logging

from findmyjob.bot import BotApplication
from findmyjob.config import Settings


def configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )


def main() -> None:
    configure_logging()
    BotApplication(Settings.from_env()).run()


if __name__ == "__main__":
    main()
