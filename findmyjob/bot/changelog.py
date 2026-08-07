"""Розсилка анонсу оновлення бота всім користувачам через мерж.

Механізм: `CHANGELOG_PENDING.md` у корені репозиторію — git-трекований файл,
куди перед мержем дописується текст релізу (перший рядок — номер версії,
далі — пункти списком, кожен з "- "). На старті бот читає його й розсилає
картинку в бренд-стилі карток вакансій ("Оновлено до версії v{версія}",
дата замість номера вакансії у плашці) з підписом-переліком змін усім
користувачам зі `known_users` — списку тих, хто хоч раз запускав бота.

Розсилка йде з обмеженою конкурентністю (той самий прийом, що й погодинні
сповіщення — `notifier.py`), а хто вже отримав саме цей текст релізу —
відмічається в SQLite (`changelog_sent`, ключ (user_id, text_hash)) одразу
після успішної відправки. Це і не дає надіслати вдруге той самий текст, і
робить розсилку стійкою до перерви на половині (рестарт бота просто
продовжує з тих user_id, кого в таблиці для поточного хеша ще нема) —
провал одного користувача (заблокував бота тощо) не зупиняє розсилку іншим.

Сам файл на диску НЕ чиститься: сервер оновлюється через `git pull`, а
локальна зміна git-трекованого файлу зламала б наступний деплой (git
відмовляється затирати незакомічені локальні правки вхідними змінами).
Тому позначка "вже надіслано" живе в БД, а не в самому файлі — наступний
мерж просто перезаписує файл новим текстом і новим хешем, і той стає новою
розсилкою для всіх, незалежно від того, хто вже бачив попередню.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import Bot

from findmyjob.images import VacancyImageRenderer
from findmyjob.storage import VacancyStore

logger = logging.getLogger(__name__)

CHANGELOG_PATH = Path(__file__).resolve().parents[2] / "CHANGELOG_PENDING.md"

# Файл має пояснювальний коментар-заголовок (інструкція для наступних PR) —
# він не текст релізу, тож не повинен вважатись "очікує відправки".
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Та сама конкурентність, що й у погодинних сповіщеннях (notifier.py) —
# розсилка по кількох користувачах одночасно, без ризику впертись у
# Telegram-ліміти на потік повідомлень.
BROADCAST_CONCURRENCY = 8

_renderer = VacancyImageRenderer()


async def broadcast_update_to_users(bot: Bot, store: VacancyStore) -> None:
    """Якщо в CHANGELOG_PENDING.md є ще не розісланий текст — шле картинку
    всім користувачам зі `known_users`, кому цей текст ще не надсилали."""
    raw = _read_pending_text()
    if not raw:
        return

    parsed = _parse_pending(raw)
    if parsed is None:
        return
    version, bullets = parsed

    text_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    already_sent = store.load_changelog_sent_user_ids(text_hash)
    pending_user_ids = [
        user_id for user_id in store.load_known_user_ids() if user_id not in already_sent
    ]
    if not pending_user_ids:
        return

    # Рендериться раз — send_photo кожному користувачу отримує свій потік
    # байтів (BytesIO спожитий після першого читання, повторно не годиться).
    photo_bytes = _renderer.render(
        f"Оновлено до версії v{version}", top_left_text=datetime.now().strftime("%d.%m.%Y")
    ).getvalue()
    caption = _compose_caption(version, bullets)

    semaphore = asyncio.Semaphore(BROADCAST_CONCURRENCY)
    sent_count = 0

    async def send_to(user_id: int) -> None:
        nonlocal sent_count
        async with semaphore:
            try:
                await bot.send_photo(
                    chat_id=user_id, photo=io.BytesIO(photo_bytes), caption=caption
                )
            except Exception:
                logger.warning(
                    "Не вдалося надіслати анонс оновлення user_id=%s", user_id, exc_info=True
                )
                return
            store.mark_changelog_sent(user_id, text_hash)
            sent_count += 1

    await asyncio.gather(*(send_to(user_id) for user_id in pending_user_ids))
    logger.info(
        "Анонс оновлення версії %s: розіслано %d/%d користувачів",
        version, sent_count, len(pending_user_ids),
    )


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
