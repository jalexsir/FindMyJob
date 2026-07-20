"""
Конфігурація RSS-джерел та логіка отримання вакансій.

Фільтрація по датах відбувається у bot.py.
KEYWORDS вимкнено (TEST MODE) — завантажуємо всі вакансії без фільтру по ключових словах.
"""

import logging
import re as _re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ── Джерела (RSS URL = HTML URL з /feeds/ або /rss/) ────────────────────────
# DOU обмежує RSS до 25 записів — обходимо через offset пагінацію.
# Deftech DOU повертає 30+ без пагінації — для них один запит достатньо.
FEEDS = {
    "Deftech DOU Java":    [
        "https://jobs.dou.ua/vacancies/feeds/?category=Java&search=miltech",
    ],
    "DOU Java":            [
        f"https://jobs.dou.ua/vacancies/feeds/?category=Java&search={quote('бронювання')}",
    ],
    "Deftech DOU Support": [
        "https://jobs.dou.ua/vacancies/feeds/?category=Support&search=miltech",
    ],
    "DOU Support":         [
        f"https://jobs.dou.ua/vacancies/feeds/?category=Support&search={quote('бронювання')}",
    ],
    "Deftech Djinni Java":    ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=miltech"],
    "Djinni Java":            ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=reservation"],
    "Deftech Djinni Support": ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=miltech"],
    "Djinni Support":         ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=reservation"],
}

REMOTE_KEYWORDS = ("віддалено", "remote", "дистанційно", "удаленно")

_SALARY_RE = _re.compile(
    r"""
    (?:від\s+)?(?:\$|€|£|грн\.?)\s*[\d\s]+(?:[–\-]\s*[\d\s]+)?(?:\+)?(?:\s*(?:тис|k|K))?
    |(?:від\s+)?[\d\s]+(?:[–\-]\s*[\d\s]+)?\s*(?:тис\.?\s*)?(?:\$|€|£|грн\.?)
    """,
    _re.VERBOSE | _re.IGNORECASE,
)


@dataclass
class Vacancy:
    source: str
    title: str
    link: str
    published: str
    pub_dt: datetime | None
    company: str = ""
    location: str = ""
    salary: str = ""


def _parse_dou_title(raw_title: str) -> tuple[str, str, str, str]:
    sep_idx, separator = -1, None
    for sep in (" в ", " на "):
        idx = raw_title.find(sep)
        if idx != -1 and (sep_idx == -1 or idx < sep_idx):
            sep_idx, separator = idx, sep
    if sep_idx == -1:
        return raw_title, "", "", ""
    title = raw_title[:sep_idx].strip()
    parts = [p.strip() for p in raw_title[sep_idx + len(separator):].split(",")]
    if not parts:
        return title, "", "", ""
    company, salary, location = parts[0], "", ""
    if len(parts) == 2:
        candidate = parts[1].strip()
        if _SALARY_RE.search(candidate):
            salary = candidate
        else:
            location = candidate
    elif len(parts) >= 3:
        if _SALARY_RE.search(parts[1]):
            salary, location = parts[1].strip(), ", ".join(parts[2:]).strip()
        else:
            location = parts[-1].strip()
            company = ", ".join(parts[:-1]).strip()
    return title, company, salary, location


def _extract_location(entry) -> str:
    text = " ".join([
        getattr(entry, "title", ""),
        getattr(entry, "summary", "") or getattr(entry, "description", ""),
    ]).lower()
    is_remote = any(kw in text for kw in REMOTE_KEYWORDS)
    location = getattr(entry, "location", None) or getattr(entry, "dou_city", None) or ""
    if is_remote and "віддалено" not in location.lower():
        location = (location + ", віддалено").lstrip(", ")
    return location or "не вказано"


def _parse_date(entry) -> datetime | None:
    raw = getattr(entry, "published", None)
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def fetch_feed(source_name: str, url: str) -> list[Vacancy]:
    GREEN, RESET = "\033[32m", "\033[0m"
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Помилка завантаження %s: %s", source_name, exc)
        print(f"{GREEN}[{source_name}] ❌ ПОМИЛКА: {exc}{RESET}")
        return []

    raw_text = response.content.decode("utf-8", errors="replace")
    print(
        f"{GREEN}\n{'='*70}\n"
        f"[{source_name}] HTTP: {response.status_code} | "
        f"Розмір: {len(response.content)} байт | URL: {url}\n"
        f"{'─'*70}\n"
        f"{raw_text[:2000]}"
        f"{'... (обрізано)' if len(raw_text) > 2000 else ''}"
        f"\n{'='*70}{RESET}"
    )

    parsed = feedparser.parse(response.content)
    total_entries = len(parsed.entries)
    vacancies = []

    for entry in parsed.entries:
        # ── TEST MODE: KEYWORDS фільтр вимкнено ──────────────────────────
        dt = _parse_date(entry)
        raw_title = getattr(entry, "title", "Без назви")
        title, company, salary, location_from_title = _parse_dou_title(raw_title)
        location = location_from_title or _extract_location(entry)
        published = dt.strftime("%d.%m.%Y") if dt else (getattr(entry, "published", "") or "невідомо")

        vacancies.append(Vacancy(
            source=source_name,
            title=title,
            link=getattr(entry, "link", ""),
            published=published,
            pub_dt=dt,
            company=company,
            location=location,
            salary=salary,
        ))

    print(f"{GREEN}[{source_name}] Всього записів у RSS: {total_entries} | Завантажено: {len(vacancies)}{RESET}")
    if total_entries > 0:
        print(f"{GREEN}  Перші 3 title:{RESET}")
        for e in parsed.entries[:3]:
            print(f"{GREEN}    → {getattr(e, 'title', '???')}{RESET}")

    return vacancies


def fetch_all_vacancies() -> dict[str, list[Vacancy]]:
    """Опитує всі джерела.
    Дедуплікація — тільки всередині кожного джерела (між різними джерелами
    одна й та сама вакансія може з'явитись — це нормально, бо джерела різні).
    """
    all_results: dict[str, list[Vacancy]] = {}

    for source_name, urls in FEEDS.items():
        seen_in_source: set[str] = set()
        source_vacancies: list[Vacancy] = []
        for url in urls:
            for vacancy in fetch_feed(source_name, url):
                if vacancy.link not in seen_in_source:
                    seen_in_source.add(vacancy.link)
                    source_vacancies.append(vacancy)
        all_results[source_name] = source_vacancies

    return all_results