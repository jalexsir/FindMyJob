"""Надсилання карток вакансій у чат."""

from __future__ import annotations

from telegram.constants import ParseMode

from findmyjob.images import VacancyImageRenderer
from findmyjob.models import Vacancy

from .formatting import format_vacancy
from .keyboards import build_favorite_vacancy_keyboard, build_vacancy_keyboard
from .state import UserState


class VacancySender:
    """Надсилає список вакансій картками (картинка + підпис + кнопки)."""

    def __init__(self, images: VacancyImageRenderer) -> None:
        self._images = images

    async def send_all(
        self,
        chat,
        vacancies: list[dict],
        state: UserState,
        *,
        force_favorite: bool = False,
        from_favorites: bool = False,
    ) -> list[int]:
        """Нові вакансії позначаються NEW, обрані — зіркою.

        Повертає id надісланих повідомлень.
        """
        seen = state.seen
        favorites = state.favorites
        message_ids: list[int] = []
        new_hashes: list[str] = []

        for number, data in enumerate(vacancies, 1):
            vacancy = Vacancy.from_dict(data)
            short_link = vacancy.short_link
            identity = vacancy.identity_hash

            is_new = identity not in seen
            if is_new:
                new_hashes.append(identity)
            is_favorite = force_favorite or short_link in favorites

            # Кешуємо повні дані — щоб hide/favorite/restore не тягли RSS заново
            state.cache_vacancy(short_link, data)

            message = await chat.send_photo(
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

        state.mark_seen(new_hashes)
        return message_ids
