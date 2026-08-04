"""Джерело вакансій NDA (nda.in.ua) — окрема псевдокатегорія, спільна для всіх.

На відміну від DOU/Djinni тут немає RSS: `NDA_URL` — звичайний HTML, і з нього
беруться лише назва та посилання (без дати, компанії, зарплати). Список
СПІЛЬНИЙ на всіх користувачів — фетчиться й кешується один раз на процес, а не
персонально під кожного, і в стандартний конвеєр DOU+Djinni (`service.py`) не
потрапляє.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from findmyjob.models import MergedSource, Vacancy

from .dedup import merge_by_link
from .parsing.text import collapse_spaces

logger = logging.getLogger(__name__)

NDA_CATEGORY = "NDA-All"
# Ніколи не повинно збігатися з Site.DOU.value/Site.DJINNI.value — це поле лише
# для відображення ("Фільтр" під карткою), тож будь-яке унікальне ім'я підійде.
NDA_SOURCE_NAME = "NDA"
NDA_URL = "https://nda.in.ua/all"
NDA_HEADERS = {"User-Agent": "FindMyJobAgregator/1.0"}
NDA_TIMEOUT = 8
NDA_CACHE_TTL_SECONDS = 120

# Реальна сторінка віддає ВІДНОСНІ посилання ("./vacancy/slug"), а не
# кореневі ("/vacancy/slug") — перевірено на живому HTTP-дампі з продакшн-
# сервера (0 знайдених /vacancy/ при 200 OK і ~940 КБ тіла). Приймаємо обидва
# варіанти на випадок, якщо сайт колись віддаватиме кореневі шляхи.
_VACANCY_HREF_RE = re.compile(r"^\.?/vacancy/")

# Вакансія лишається, лише якщо її назва містить хоча б одне з цих слів
# (без урахування регістру) — фільтр застосовується до дедупу. Порядок у
# списку значення не має: перевіряється "чи є будь-яке з них", а не
# пріоритет одного слова над іншим.
_TITLE_FILTER_WORDS = (
    "System", "Administrator", "Data", "Engineer", "C++", "Python", "Backend",
    "ArduPilot", "SDR", "DevOps", "Reverse", "Support", "Computer", "Vision",
    "SLAM", "VIO", "Autonomous", "Systems", "Robotics", "Simulation", "Embedded",
    "Firmware", "Developer", "Hardware", "Technical", "Lead", "Electronics",
    "Component", "QA", "Analyst", "Quality", "Radio", "Frequency", "RF",
    "Measurement", "Antenna", "Link", "Integration", "Mechanical", "DFA",
    "Industrial", "Release", "Altium", "Designer", "PCB", "PCBA", "APQP",
    "FMEA", "Design", "Risk", "Assembler", "CNC", "UAV", "UGV", "Test",
    "Pilot", "Project", "Manager", "Delivery", "Product", "Owner", "Functional",
    "Chief", "Staff", "Monetization", "Senior", "Copywriter", "Content",
    "Writer", "OSINT", "Технік", "програміст", "Тестування", "електроніки",
    "Інженер", "інтеграції", "систем", "зв’язку", "Відеопередача",
    "конструктор", "CAD-інженер", "Системний", "інтегратор", "якості",
    "NPI", "Композитні", "матеріали", "технолог", "Машинобудування",
    "Електромеханік", "Оператор", "ЧПК", "Ремонт", "обладнання", "АСУ",
    "3D-принтери", "ремонту", "електроінструменту", "Моторист",
    "Механік-випробувач", "Механік-прототипувальник", "експлуатаційної",
    "документації", "Технічний", "редактор", "Менеджер", "розвитку",
    "бізнесу", "Грантовий", "ЗЕД", "роботи", "клієнтами", "OSINT-аналітик",
)
_TITLE_FILTER_WORDS_LOWER = tuple(word.lower() for word in _TITLE_FILTER_WORDS)


def clean_title(text: str) -> str:
    """Прибирає зайві пробіли/переноси в сирому тексті посилання."""
    return collapse_spaces(text)


def _matches_title_filter(title: str) -> bool:
    """True, щойно назва містить перше-ліпше слово зі списку — решту слів для
    цієї вакансії вже не перевіряємо (`any()` коротко замикається)."""
    lowered = title.lower()
    return any(word in lowered for word in _TITLE_FILTER_WORDS_LOWER)


def _fetch_nda_vacancies() -> list[Vacancy]:
    try:
        response = requests.get(NDA_URL, headers=NDA_HEADERS, timeout=NDA_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("[NDA] %s недоступний: %s", NDA_URL, exc)
        return []

    # response.content (байти), а не response.text: requests вгадує кодування
    # з заголовків, і без явного charset у Content-Type падає на latin-1 —
    # кирилиця перетворюється на "ÐÐ½Ð¶ÐµÐ½ÐµÑ...". BeautifulSoup сам визначає
    # кодування з байтів (у т.ч. з <meta charset>) і робить це правильно.
    soup = BeautifulSoup(response.content, "html.parser")
    links = soup.find_all("a", href=_VACANCY_HREF_RE)

    candidates: list[Vacancy] = []
    for a in links:
        href = a.get("href")
        if not href:
            continue
        # Картка — це не тільки назва: поряд лежать теги-бейджі ("Бронювання",
        # категорія), кожен у своєму <p>, без пробілу між ними в розмітці.
        # a.get_text() на всій картці зліпив би їх у "НазваБронюванняКатегорія".
        # Сама назва — єдиний <h6> усередині картки (перевірено на живому
        # дампі: 226/226 карток мають рівно один). Якщо розмітка колись
        # зміниться і <h6> зникне — відкат на весь текст картки, аби не
        # впасти в 0 вакансій знову.
        heading = a.find("h6")
        raw_title = heading.get_text(strip=True) if heading else a.get_text(strip=True)
        title = clean_title(raw_title)
        if not title:
            continue
        candidates.append(Vacancy(
            source=NDA_SOURCE_NAME,
            title=title,
            # urljoin, а не конкатенація рядків: href відносний ("./vacancy/x"),
            # проста конкатенація дала б "https://nda.in.ua./vacancy/x".
            link=urljoin(NDA_URL, href),
            category=NDA_CATEGORY,
        ))

    # Фільтр за словами в назві — ДО дедупу (щоб не дублювати роботу над
    # вакансіями, які все одно будуть відкинуті).
    filtered = [v for v in candidates if _matches_title_filter(v.title)]

    # primary=[] навмисно: дедуп потрібен ВСЕРЕДИНІ єдиного списку (кілька
    # <a> можуть вести на ту саму вакансію), а merge_by_link дедуплікує саме
    # secondary — і між собою, і проти primary.
    result = merge_by_link(NDA_CATEGORY, [], filtered).vacancies

    # Лог завжди (не лише при помилці): 200 OK з 0 знайдених посилань — це не
    # виняток requests, і без цього рядка діагностувати "чому порожньо" можна
    # тільки наосліп. HTTP-статус і розмір тіла показують, чи сайт узагалі
    # віддав очікувану сторінку (а не, наприклад, чужу заглушку/капчу).
    logger.info(
        "[NDA] %s → %d, %d байт, знайдено %d посилань /vacancy/, %d пройшли фільтр "
        "слів (%d відсіяно), %d вакансій після дедупу",
        NDA_URL, response.status_code, len(response.content), len(links),
        len(filtered), len(candidates) - len(filtered), len(result),
    )

    return result


class _NdaCache:
    """Потокобезпечний однослотовий TTL-кеш — список один на всіх, без ключа
    категорій (на відміну від `_Stage1Cache`, тут нема різних комбінацій)."""

    def __init__(self, ttl_seconds: int = NDA_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._vacancies: list[Vacancy] | None = None
        self._stored_at: float = 0.0

    def get(self) -> list[Vacancy] | None:
        with self._lock:
            if self._vacancies is None:
                return None
            if (time.monotonic() - self._stored_at) >= self._ttl:
                self._vacancies = None
                return None
            return self._vacancies

    def put(self, vacancies: list[Vacancy]) -> None:
        with self._lock:
            self._vacancies = vacancies
            self._stored_at = time.monotonic()


_cache = _NdaCache()


def get_nda_source() -> MergedSource:
    """Повертає спільний список вакансій NDA як готовий `MergedSource`.

    Кешується (успіх і порожній результат однаково) на `NDA_CACHE_TTL_SECONDS`,
    щоб недоступне джерело не довбали на кожен запит користувача.
    """
    vacancies = _cache.get()
    if vacancies is None:
        vacancies = _fetch_nda_vacancies()
        _cache.put(vacancies)
    return MergedSource(name=NDA_CATEGORY, vacancies=vacancies, duplicates=0)


def refresh_nda_source() -> MergedSource:
    """Примусово фетчить свіжий список, ігноруючи TTL-кеш, і одразу оновлює його.

    Використовується лише явним оновленням еталону ("Оновити еталон") — там
    потрібен саме поточний стан сайту, а не те, що лежить у кеші до 120 с.
    """
    vacancies = _fetch_nda_vacancies()
    _cache.put(vacancies)
    return MergedSource(name=NDA_CATEGORY, vacancies=vacancies, duplicates=0)
