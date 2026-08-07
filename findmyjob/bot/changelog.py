"""Сповіщення адміну про оновлення бота через мерж.

Механізм: `CHANGELOG_PENDING.md` у корені репозиторію — git-трекований файл,
куди перед мержем дописується текст релізу (перший рядок — номер версії,
далі — пункти списком, кожен з "- "). На старті бот читає його; якщо там є
текст і його хеш відрізняється від хеша останнього надісланого (SQLite,
`changelog_state`) — надсилає адміну картинку в бренд-стилі карток вакансій
("Оновлення до {версія}") з підписом-переліком змін і запам'ятовує хеш.

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

from findmyjob.images import VacancyImageRenderer
from findmyjob.storage import VacancyStore

logger = logging.getLogger(__name__)

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG_PENDING.md"

# Файл має пояснювальний коментар-заголовок (інструкція для наступних PR) —
# він не текст релізу, тож не повинен вважатись "очікує відправки".
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_renderer = VacancyImageRenderer()


async def notify_admin_of_update(
    bot: Bot, store: VacancyStore, admin_user_id: int | None
) -> None:
    """Якщо в CHANGELOG_PENDING.md є ще не надісланий текст — шле картинку адміну."""
    if admin_user_id is None:
        return

    raw = _read_pending_text()
    if not raw:
        return

    parsed = _parse_pending(raw)
    if parsed is None:
        return
    version, bullets = parsed

    text_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if store.load_last_sent_changelog_hash() == text_hash:
        return

    photo = _renderer.render(f"Оновлення до {version}")
    caption = _compose_caption(version, bullets)

    try:
        await bot.send_photo(chat_id=admin_user_id, photo=photo, caption=caption)
    except Exception:
        logger.exception("Не вдалося надіслати адміну сповіщення про оновлення бота")
        return

    store.save_last_sent_changelog_hash(text_hash)


def _read_pending_text() -> str:
    if not CHANGELOG_PATH.exists():
        return ""
    raw = CHANGELOG_PATH.read_text(encoding="utf-8")
    return _COMMENT_RE.sub("", raw).strip()


def _parse_pending(text: str) -> tuple[str, list[str]] | None:
    """Перший непорожній рядок — номер версії, далі — пункти ("- ...")."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    version, *rest = lines
    bullets = [line[1:].strip() for line in rest if line.startswith("-")]
    if not bullets:
        return None
    return version, bullets


def _compose_caption(version: str, bullets: list[str]) -> str:
    lines = [f"🎉 FindMyJob Bot оновлений до версії {version}", "", "Що нового:"]
    lines += [f"✅ {bullet}" for bullet in bullets]
    return "\n".join(lines)
