"""Надсилання карток вакансій у чат."""

from __future__ import annotations

import asyncio

from telegram.constants import ParseMode

from findmyjob.images import VacancyImageRenderer
from findmyjob.models import Vacancy

from .formatting import format_vacancy
from .keyboards import build_favorite_vacancy_keyboard, build_vacancy_keyboard
from .state import UserState

# Пауза між картками. Telegram притримує ботів, які сиплють в один чат без
# упину, а пачка легко буває на кілька десятків карток — і при пошуку, і в
# годинному сповіщенні. Півсекунди тримають потік рівним і не дають зловити 429.
SEND_DELAY_SECONDS = 0.5


async def pace() -> None:
    """Пауза між двома повідомленнями однієї пачки."""
    await asyncio.sleep(SEND_DELAY_SECONDS)


class VacancySender:
    """Надсилає список вакансій картками (картинка + підпис + кнопки)."""

    def __init__(self, images: VacancyImageRenderer) -> None:
        self._images = images

    async def send_all(
        self,
        bot,
        chat_id: int,
        vacancies: list[dict],
        state: UserState,
        *,
        force_favorite: bool = False,
        from_favorites: bool = False,
        track_seen: bool = True,
    ) -> list[int]:
        """Нові вакансії позначаються NEW, обрані — зіркою.

        Приймає `bot` і `chat_id`, а не об'єкт чату: шкедулер працює без апдейта,
        і взяти звідкись `Chat` там немає де.

        `track_seen=False` — не позначати вакансії переглянутими: сповіщення не
        мають «з'їдати» мітку NEW у ручному пошуку.

        Повертає id надісланих повідомлень.
        """
        seen = state.seen
        favorites = state.favorites
        message_ids: list[int] = []
        new_hashes: list[str] = []

        for number, data in enumerate(vacancies, 1):
            if number > 1:
                await pace()
            vacancy = Vacancy.from_dict(data)
            short_link = vacancy.short_link
            identity = vacancy.identity_hash

            is_new = identity not in seen
            if is_new:
                new_hashes.append(identity)
            is_favorite = force_favorite or short_link in favorites

            # Кешуємо повні дані — щоб hide/favorite/restore не тягли RSS заново
            state.cache_vacancy(short_link, data)

            message = await bot.send_photo(
                chat_id=chat_id,
                photo=self._images.render(
                    vacancy.title, number, is_new=is_new, is_favorite=is_favorite
                ),
                caption=format_vacancy(vacancy),
                parse_mode=ParseMode.HTML,
                reply_markup=(
                    build_favorite_vacancy_keyboard(vacancy) if from_favorites
                    else build_vacancy_keyboard(vacancy, is_favorite=is_favorite)
                ),
            )
            message_ids.append(message.message_id)

        if track_seen:
            state.mark_seen(new_hashes)
        return message_ids
