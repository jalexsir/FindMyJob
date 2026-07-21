"""
RSS-джерела та логіка отримання вакансій.

Завантаження: 8 сирих джерел → двоетапне злиття → 2 фінальних списки.
Фільтрація по датах виконується в bot.py.
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

# ── Константи ─────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15
GREEN, RESET = "\033[32m", "\033[0m"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

REMOTE_KEYWORDS = ("віддалено", "remote", "дистанційно", "удаленно")

_SALARY_RE = _re.compile(
    r"""
    (?:від\s+)?(?:\$|€|£|грн\.?)\s*[\d\s]+(?:[–\-]\s*[\d\s]+)?(?:\+)?(?:\s*(?:тис|k|K))?
    |(?:від\s+)?[\d\s]+(?:[–\-]\s*[\d\s]+)?\s*(?:тис\.?\s*)?(?:\$|€|£|грн\.?)
    """,
    _re.VERBOSE | _re.IGNORECASE,
)

# ── 8 сирих RSS-джерел ────────────────────────────────────────────────────────

RAW_FEEDS: dict[str, list[str]] = {
    "Deftech DOU Java":       ["https://jobs.dou.ua/vacancies/feeds/?category=Java&search=miltech"],
    "DOU Java":               [f"https://jobs.dou.ua/vacancies/feeds/?category=Java&search={quote('бронювання')}"],
    "Deftech DOU Support":    ["https://jobs.dou.ua/vacancies/feeds/?category=Support&search=miltech"],
    "DOU Support":            [f"https://jobs.dou.ua/vacancies/feeds/?category=Support&search={quote('бронювання')}"],
    "Deftech Djinni Java":    ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=miltech"],
    "Djinni Java":            ["https://djinni.co/jobs/rss/?all_keywords=java&search_type=basic-search&editorial=reservation"],
    "Deftech Djinni Support": ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=miltech"],
    "Djinni Support":         ["https://djinni.co/jobs/rss/?all_keywords=support&search_type=basic-search&editorial=reservation"],
}


# ── Моделі ────────────────────────────────────────────────────────────────────

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
    """Фінальний список вакансій після злиття та дедуплікації."""
    name: str
    vacancies: list[Vacancy]
    total_before: int
    duplicates: int


# ── Парсери ───────────────────────────────────────────────────────────────────

def _dedup_key(v: Vacancy) -> tuple[str, str]:
    """Ключ дедуплікації: (title, company) без урахування регістру."""
    return (v.title.strip().lower(), v.company.strip().lower())


def _parse_dou_title(raw_title: str) -> tuple[str, str, str, str]:
    """Розбирає DOU-формат 'Посада в/на Компанія, [Зарплата,] Місто'.
    Повертає (title, company, salary, location).
    """
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
            salary = parts[1].strip()
            location = ", ".join(parts[2:]).strip()
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


# Патерн для латинських слів (назва компанії)
_LATIN_RE = _re.compile(r'[A-Za-z]')

def _extract_company_from_description(description: str) -> str:
    """
    Витягує назву компанії з HTML-опису вакансії Djinni.

    Стратегії (в порядку пріоритету):
    1. Перший <strong>Назва</strong> — якщо містить латиницю або це перший strong в описі
    2. Текст після <p> до " -" або " —" — якщо містить латиницю
    """
    import html as _html

    if not description:
        return ""

    # Декодуємо HTML-entities
    text = _html.unescape(description)

    # Стратегія 1: перший <strong>...</strong>
    strong_match = _re.search(r'<strong[^>]*>(.*?)</strong>', text, _re.IGNORECASE | _re.DOTALL)
    if strong_match:
        candidate = _re.sub(r'<[^>]+>', '', strong_match.group(1)).strip()
        # Беремо якщо містить латиницю або досить коротке (ймовірно назва)
        if candidate and (bool(_LATIN_RE.search(candidate)) or len(candidate) < 40):
            return candidate

    # Стратегія 2: текст між <p> і першим " -" або " —"
    p_match = _re.search(r'<p[^>]*>(.*?)(?:\s[-—]|\Z)', text, _re.IGNORECASE | _re.DOTALL)
    if p_match:
        candidate = _re.sub(r'<[^>]+>', '', p_match.group(1)).strip()
        candidate = candidate.strip('- —\n')
        # Беремо тільки якщо містить латиницю і не занадто довге
        if candidate and bool(_LATIN_RE.search(candidate)) and len(candidate) < 60:
            return candidate

    return ""


def _extract_company(entry) -> str:
    """Витягує компанію: спочатку RSS-поля, потім парсинг description."""
    # Спочатку пробуємо стандартні RSS-поля
    for field in ("author", "dc_creator", "itunes_author"):
        val = getattr(entry, field, None)
        if val and val.strip():
            return val.strip()

    # Fallback: парсимо description
    description = getattr(entry, "summary", None) or getattr(entry, "description", "")
    return _extract_company_from_description(description)


# ── Завантаження ──────────────────────────────────────────────────────────────

def _fetch_feed(source_name: str, url: str) -> list[Vacancy]:
    """Завантажує один RSS-фід і повертає список Vacancy."""
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
        f"{raw_text}\n"
        f"{'='*70}{RESET}"
    )

    parsed = feedparser.parse(response.content)
    vacancies = []
    for entry in parsed.entries:
        dt = _parse_date(entry)
        raw_title = getattr(entry, "title", "Без назви")
        title, company, salary, loc_from_title = _parse_dou_title(raw_title)

        if not company:
            company = _extract_company(entry)

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

    print(f"{GREEN}[{source_name}] Записів у RSS: {len(parsed.entries)} | Завантажено: {len(vacancies)}{RESET}")
    if parsed.entries:
        e0 = parsed.entries[0]
        company_fields = {f: getattr(e0, f, None) for f in ("author", "dc_creator", "itunes_author")}
        print(f"{GREEN}  Всі title ({len(parsed.entries)}):{RESET}")
        for e in parsed.entries:
            print(f"{GREEN}    → {getattr(e, 'title', '???')}{RESET}")
        print(f"{GREEN}  Поля компанії 1-го запису: {company_fields}{RESET}")

    return vacancies


def _fetch_raw() -> dict[str, list[Vacancy]]:
    """Завантажує всі 8 джерел. Дедуплікує всередині кожного за (title, company)."""
    raw: dict[str, list[Vacancy]] = {}
    for source_name, urls in RAW_FEEDS.items():
        seen: set[tuple[str, str]] = set()
        vacancies: list[Vacancy] = []
        for url in urls:
            for v in _fetch_feed(source_name, url):
                key = _dedup_key(v)
                if key not in seen:
                    seen.add(key)
                    vacancies.append(v)
        raw[source_name] = vacancies
    return raw


# ── Логування ─────────────────────────────────────────────────────────────────

def _log_list(name: str, vacancies: list[Vacancy]) -> None:
    print(f"{GREEN}\n{'─'*60}\n📋 {name} ({len(vacancies)} вак.):{RESET}")
    for i, v in enumerate(vacancies, 1):
        print(f"{GREEN}  {i:2}. title={v.title!r}, company={v.company!r}{RESET}")
    if not vacancies:
        print(f"{GREEN}  (порожній){RESET}")


# ── Злиття з дедуплікацією ────────────────────────────────────────────────────

def _merge(name: str, primary: list[Vacancy], secondary: list[Vacancy]) -> tuple[list[Vacancy], int]:
    """Об'єднує два списки. При збігу (title, company) — видаляє з secondary."""
    seen: set[tuple[str, str]] = {_dedup_key(v) for v in primary}
    unique_secondary: list[Vacancy] = []
    duplicates = 0

    print(f"{GREEN}\n[MERGE → {name}] primary={len(primary)}, secondary={len(secondary)}{RESET}")
    for v in secondary:
        key = _dedup_key(v)
        if key in seen:
            print(f"{GREEN}  ✂ ДУБЛІКАТ: title={v.title!r}, company={v.company!r}{RESET}")
            duplicates += 1
        else:
            seen.add(key)
            unique_secondary.append(v)

    combined = primary + unique_secondary
    print(f"{GREEN}  ✅ {len(combined)} вак. (видалено {duplicates}){RESET}")
    return combined, duplicates


# ── Публічний API ─────────────────────────────────────────────────────────────

def fetch_all_vacancies() -> list[MergedSource]:
    """
    Двоетапне злиття 8 → 2 фінальних списки.

    Етап 1 (8 → 4):
      Deftech Djinni Java  + Djinni Java      → Temp Djinni Java
      Deftech DOU Java     + DOU Java          → Temp DOU Java
      Deftech Djinni Support + Djinni Support  → Temp Djinni Support
      Deftech DOU Support  + DOU Support       → Temp DOU Support

    Етап 2 (4 → 2, пріоритет Djinni):
      Temp Djinni Java    + Temp DOU Java      → Final Java
      Temp Djinni Support + Temp DOU Support   → Final Support
    """
    raw = _fetch_raw()

    # Етап 0: лог сирих даних
    print(f"{GREEN}\n{'='*60}\nЕТАП 0: Сирі дані з 8 джерел\n{'='*60}{RESET}")
    for name, vacancies in raw.items():
        _log_list(name, vacancies)

    # Етап 1
    print(f"{GREEN}\n{'='*60}\nЕТАП 1: Попарне злиття (Deftech + основне)\n{'='*60}{RESET}")
    temp_djinni_java,    _ = _merge("Temp Djinni Java",    raw.get("Deftech Djinni Java", []),    raw.get("Djinni Java", []))
    temp_dou_java,       _ = _merge("Temp DOU Java",       raw.get("Deftech DOU Java", []),       raw.get("DOU Java", []))
    temp_djinni_support, _ = _merge("Temp Djinni Support", raw.get("Deftech Djinni Support", []), raw.get("Djinni Support", []))
    temp_dou_support,    _ = _merge("Temp DOU Support",    raw.get("Deftech DOU Support", []),    raw.get("DOU Support", []))

    print(f"{GREEN}\n{'='*60}\nПІСЛЯ ЕТАПУ 1: 4 тимчасових списки\n{'='*60}{RESET}")
    _log_list("Temp Djinni Java",    temp_djinni_java)
    _log_list("Temp DOU Java",       temp_dou_java)
    _log_list("Temp Djinni Support", temp_djinni_support)
    _log_list("Temp DOU Support",    temp_dou_support)

    # Етап 2
    print(f"{GREEN}\n{'='*60}\nЕТАП 2: Djinni + DOU (пріоритет Djinni)\n{'='*60}{RESET}")
    final_java,    dup_java    = _merge("Final Java",    temp_djinni_java,    temp_dou_java)
    final_support, dup_support = _merge("Final Support", temp_djinni_support, temp_dou_support)

    print(f"{GREEN}\n{'='*60}\nФІНАЛ\n{'='*60}{RESET}")
    _log_list("Final Java",    final_java)
    _log_list("Final Support", final_support)

    return [
        MergedSource(
            name="Java (з бронюванням)",
            vacancies=final_java,
            total_before=len(temp_djinni_java) + len(temp_dou_java),
            duplicates=dup_java,
        ),
        MergedSource(
            name="Support (з бронюванням)",
            vacancies=final_support,
            total_before=len(temp_djinni_support) + len(temp_dou_support),
            duplicates=dup_support,
        ),
    ]