"""
Конфігурація RSS-джерел та логіка отримання вакансій.

Вакансії завантажуються з 8 джерел, потім об'єднуються в 4 пари
з видаленням дублікатів між парою. Фільтрація по датах — у bot.py.
"""

import logging
import re as _re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# ── 8 сирих джерел ───────────────────────────────────────────────────────────
RAW_FEEDS = {
    "Deftech DOU Java":       ["https://jobs.dou.ua/vacancies/feeds/?category=Java&search=miltech"],
    "DOU Java":               [f"https://jobs.dou.ua/vacancies/feeds/?category=Java&search={quote('бронювання')}"],
    "Deftech DOU Support":    ["https://jobs.dou.ua/vacancies/feeds/?category=Support&search=miltech"],
    "DOU Support":            [f"https://jobs.dou.ua/vacancies/feeds/?category=Support&search={quote('бронювання')}"],
    "Deftech Djinni Java":    ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=miltech"],
    "Djinni Java":            ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=reservation"],
    "Deftech Djinni Support": ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=miltech"],
    "Djinni Support":         ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=reservation"],
}

# ── 4 об'єднані пари (ім'я: [джерело_A, джерело_B]) ─────────────────────────
MERGED_PAIRS = [
    ("Reservation DOU Java",     "Deftech DOU Java",    "DOU Java"),
    ("Reservation DOU Support",  "Deftech DOU Support", "DOU Support"),
    ("Reservation Djinni Java",  "Deftech Djinni Java", "Djinni Java"),
    ("Reservation Djinni Support","Deftech Djinni Support","Djinni Support"),
]

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


@dataclass
class MergedSource:
    """Об'єднаний результат пари джерел після видалення дублікатів."""
    name: str
    vacancies: list[Vacancy]
    total_before: int   # кількість до видалення дублікатів
    duplicates: int     # скільки дублікатів видалено


# ── Парсер title ──────────────────────────────────────────────────────────────

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


# ── Завантаження одного RSS ───────────────────────────────────────────────────

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
        f"Розмір: {len(response.content)} байт\n"
        f"URL: {url}\n"
        f"{'─'*70}\n"
        f"{raw_text[:1500]}"
        f"{'...(обрізано)' if len(raw_text) > 1500 else ''}"
        f"\n{'='*70}{RESET}"
    )

    parsed = feedparser.parse(response.content)
    vacancies = []
    for entry in parsed.entries:
        dt = _parse_date(entry)
        raw_title = getattr(entry, "title", "Без назви")
        title, company, salary, loc_from_title = _parse_dou_title(raw_title)
        location = loc_from_title or _extract_location(entry)
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

    print(f"{GREEN}[{source_name}] Всього записів у RSS: {len(parsed.entries)} | Завантажено: {len(vacancies)}{RESET}")
    if parsed.entries:
        print(f"{GREEN}  Перші 3 title:{RESET}")
        for e in parsed.entries[:3]:
            print(f"{GREEN}    → {getattr(e, 'title', '???')}{RESET}")

    return vacancies


# ── Завантаження всіх 8 джерел ────────────────────────────────────────────────

def _fetch_raw() -> dict[str, list[Vacancy]]:
    """Завантажує всі 8 сирих джерел, дедуплікує всередині кожного за (title, company)."""
    raw: dict[str, list[Vacancy]] = {}
    for source_name, urls in RAW_FEEDS.items():
        seen: set[tuple[str, str]] = set()
        vacancies: list[Vacancy] = []
        for url in urls:
            for v in fetch_feed(source_name, url):
                key = _dedup_key(v)
                if key not in seen:
                    seen.add(key)
                    vacancies.append(v)
        raw[source_name] = vacancies
    return raw


def _dedup_key(v: Vacancy) -> tuple[str, str]:
    """Ключ для порівняння дублікатів: назва вакансії + компанія (без урахування регістру)."""
    return (v.title.strip().lower(), v.company.strip().lower())


# ── Об'єднання пар з дедуплікацією ───────────────────────────────────────────

def fetch_all_vacancies() -> list[MergedSource]:
    """
    Завантажує 8 джерел і об'єднує в 4 MergedSource.
    Дублікати визначаються за парою (title + company) без урахування регістру.
    """
    raw = _fetch_raw()
    merged_sources: list[MergedSource] = []

    for merged_name, source_a, source_b in MERGED_PAIRS:
        list_a = raw.get(source_a, [])
        list_b = raw.get(source_b, [])

        total_before = len(list_a) + len(list_b)

        # Дедуплікація: беремо всі з A, з B додаємо тільки ті
        # у яких пара (title, company) відсутня в A
        seen: set[tuple[str, str]] = {_dedup_key(v) for v in list_a}
        unique_b = [v for v in list_b if _dedup_key(v) not in seen]
        duplicates = len(list_b) - len(unique_b)

        combined = list_a + unique_b

        GREEN, RESET = "\033[32m", "\033[0m"
        print(
            f"{GREEN}[MERGE] {merged_name}: "
            f"{source_a}({len(list_a)}) + {source_b}({len(list_b)}) "
            f"→ {len(combined)} (видалено {duplicates} дублікатів){RESET}"
        )

        merged_sources.append(MergedSource(
            name=merged_name,
            vacancies=combined,
            total_before=total_before,
            duplicates=duplicates,
        ))

    return merged_sources