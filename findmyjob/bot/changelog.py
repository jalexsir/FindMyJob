"""Сповіщення адміну про оновлення бота через мерж.

Механізм: `CHANGELOG_PENDING.md` у корені репозиторію — git-трекований файл,
куди перед мержем дописується текст релізу. На старті бот читає його; якщо
там є текст і його хеш відрізняється від хеша останнього надісланого
(SQLite, `changelog_state`) — надсилає адміну і запам'ятовує хеш.

Сам файл на диску НЕ чиститься: сервер оновлюється через `git pull`, а
локальна зміна git-трекованого файлу зламала б наступний деплой (git
відмовляється затирати незакомічені локальні правки вхідними змінами).
Тому позначка "вже надіслано" живе в БД, а не в самому файлі — наступний
мерж просто перезаписує файл новим текстом і новим хешем.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from telegram import Bot

from findmyjob.storage import VacancyStore

logger = logging.getLogger(__name__)

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG_PENDING.md"

# Файл має пояснювальний коментар-заголовок (інструкція для наступних PR) —
# він не текст релізу, тож не повинен вважатись "очікує відправки".
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


async def notify_admin_of_update(
    bot: Bot, store: VacancyStore, admin_user_id: int | None
) -> None:
    """Якщо в CHANGELOG_PENDING.md є ще не надісланий текст — шле його адміну."""
    if admin_user_id is None:
        return

    text = _read_pending_text()
    if not text:
        return

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if store.load_last_sent_changelog_hash() == text_hash:
        return

    try:
        await bot.send_message(chat_id=admin_user_id, text=text)
    except Exception:
        logger.exception("Не вдалося надіслати адміну сповіщення про оновлення бота")
        return

    store.save_last_sent_changelog_hash(text_hash)


def _read_pending_text() -> str:
    if not CHANGELOG_PATH.exists():
        return ""
    raw = CHANGELOG_PATH.read_text(encoding="utf-8")
    return _COMMENT_RE.sub("", raw).strip()
