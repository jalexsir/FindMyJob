"""Доменні моделі: вакансія, зведення по джерелу та їх ідентифікатори."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

UNKNOWN_DATE = "невідомо"


def make_vacancy_hash(title: str, company: str) -> str:
    """SHA-256 хеш від (title + company) — унікальний ідентифікатор вакансії."""
    key = f"{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def make_link_hash(link: str) -> str:
    """SHA-256 хеш ПОВНОГО посилання, обрізаний до 16 hex — стабільний короткий ID
    вакансії для callback_data (ліміт Telegram — 64 байти на callback_data).

    НЕ можна просто брати link[:50]: URL різних вакансій одного сайту часто
    збігаються в перших ~50 символах (спільний префікс компанії), а числовий ID
    вакансії йде вже ПІСЛЯ 50-го символу — тобто різні вакансії буквально
    зливались в один "short_link" і приховування однієї ховало й іншу.
    """
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def make_hidden_key(short_link: str, published: str, title: str) -> str:
    """Ключ звірки з «прихованими»: хеш + дата + назва, склеєні як звичайні рядки.

    Самого `short_link` мало: DOU і Djinni перепублікують вакансію під тим самим
    URL, але з новою датою, тож стара прихована вакансія ховала б нову. Дата
    порівнюється саме рядком (як вона збережена в `published`), без розбору в
    `date`. Назва йде останньою, бо може містити будь-які символи, зокрема `|`;
    `short_link` завжди має фіксовану довжину, тож склейка однозначна.
    """
    return f"{short_link}|{published}|{title}"


def parse_published(published: str) -> date | None:
    """Розбирає рядок дати публікації (dd.mm.yyyy або RFC 2822)."""
    if not published or published == UNKNOWN_DATE:
        return None
    try:
        return datetime.strptime(published, "%d.%m.%Y").date()
    except (ValueError, TypeError):
        pass
    try:
        return parsedate_to_datetime(published).date()
    except Exception:
        return None


@dataclass
class Vacancy:
    """Одна вакансія з RSS-фіду.

    Усі поля мають значення за замовчуванням, бо вакансія може відновлюватись із
    неповного словника (напр. запис у "прихованих", збережений без повних даних).
    """

    source: str = ""
    title: str = ""
    link: str = ""
    published: str = ""
    pub_dt: datetime | None = None
    company: str = ""
    location: str = ""
    salary: str = ""
    vacancy_hash: str = ""  # заповнюється після парсингу
    category: str = ""      # напр. "Java" — заповнюється сервісом фідів після злиття

    @property
    def short_link(self) -> str:
        """Короткий стабільний ID для callback_data."""
        return make_link_hash(self.link)

    @property
    def hidden_key(self) -> str:
        """Ключ звірки з «прихованими»: `short_link|published|title`."""
        return make_hidden_key(self.short_link, self.published, self.title)

    @property
    def identity_hash(self) -> str:
        """Хеш (title + company) — використовується для мітки NEW."""
        return self.vacancy_hash or make_vacancy_hash(self.title, self.company)

    def publication_date(self) -> date | None:
        """Дата публікації для сортування й фільтрів.

        Пріоритет: `pub_dt`, потім парсинг рядка `published`.
        None означає, що дату визначити не вдалося.
        """
        if self.pub_dt is not None:
            try:
                return self.pub_dt.date()
            except Exception:
                pass
        return parse_published(self.published)

    def to_dict(self) -> dict[str, Any]:
        """Плаский словник — формат зберігання в bot_data та SQLite."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Vacancy":
        """Створює вакансію зі словника, ігноруючи сторонні ключі.

        Записи "прихованих" містять додатковий ключ `categories`, якого немає
        серед полів моделі — тому просте `Vacancy(**data)` тут не підходить.
        """
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class MergedSource:
    """Фінальний список вакансій однієї категорії після злиття та дедуплікації."""

    name: str
    vacancies: list[Vacancy] = field(default_factory=list)
    duplicates: int = 0
