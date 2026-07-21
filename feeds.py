"""
RSS-джерела та логіка отримання вакансій.

Завантаження: 8 сирих джерел → двоетапне злиття → 2 фінальних списки.
Фільтрація по датах виконується в bot.py.
"""

import logging
import re as _re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

import hashlib
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

# ── Доступні категорії ────────────────────────────────────────────────────────

AVAILABLE_CATEGORIES = ["Java", "Support", "Golang", "Integration"]

DOU_CATEGORY_MAP   = {"Java": "Java", "Support": "Support", "Golang": "Golang", "Integration": "Integration"}
DJINNI_KEYWORD_MAP = {"Java": "java", "Support": "support", "Golang": "golang", "Integration": "integration"}


def build_feeds_for_categories(categories: list[str]) -> dict[str, list[str]]:
    """Будує RAW_FEEDS динамічно для обраних категорій."""
    feeds: dict[str, list[str]] = {}
    for cat in categories:
        dou_cat    = DOU_CATEGORY_MAP.get(cat, cat)
        djinni_kw  = DJINNI_KEYWORD_MAP.get(cat, cat.lower())
        feeds[f"Deftech DOU {cat}"]    = [f"https://jobs.dou.ua/vacancies/feeds/?category={dou_cat}&search=miltech"]
        feeds[f"DOU {cat}"]            = [f"https://jobs.dou.ua/vacancies/feeds/?category={dou_cat}&search={quote('бронювання')}"]
        feeds[f"Deftech Djinni {cat}"] = [f"https://djinni.co/jobs/rss/?all_keywords={djinni_kw}&search_type=basic-search&editorial=miltech"]
        feeds[f"Djinni {cat}"]         = [f"https://djinni.co/jobs/rss/?all_keywords={djinni_kw}&search_type=basic-search&editorial=reservation"]
    return feeds


# ── Моделі ────────────────────────────────────────────────────────────────────

def make_vacancy_hash(title: str, company: str) -> str:
    """SHA-256 хеш від (title + company) — унікальний ідентифікатор вакансії."""
    key = f"{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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
    vacancy_hash: str = ""  # заповнюється після парсингу


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


def _is_invalid_company(text: str, title: str = "") -> bool:
    """Повертає True якщо текст НЕ є назвою компанії."""
    if not text:
        return True
    t = text.strip()
    # Закінчується на двокрапку або кому — це заголовок опису
    if t.endswith(":") or t.endswith(","):
        return True
    # Довше 60 символів — скоріш за все опис
    if len(t) > 60:
        return True
    # Збігається з назвою посади (title дублюється)
    if title and t.lower() == title.lower():
        return True
    # Якщо перші 2 слова company збігаються з першими 2 словами title — дублікат
    if title:
        t_words = t.lower().split()[:2]
        ti_words = title.lower().split()[:2]
        if t_words and t_words == ti_words:
            return True
    # Типові слова що вказують на опис а не назву компанії
    desc_markers = (
        "ми шукаємо", "що потрібний", "зсу в ", "займаємося", "цінності",
        "обов'язки", "вимоги", "умови", "пропонуємо",
        "architect the", "management:", "stack &", "tool management",
        "якому довіряють", "якій довіряють",
    )
    tl = t.lower()
    if any(m in tl for m in desc_markers):
        return True
    return False


def _split_company_and_cities(company: str) -> tuple[str, str]:
    """Розділяє 'Назва, Місто1, Місто2' → ('Назва', 'Місто1, Місто2').

    Логіка: якщо останні N частин після першої коми — короткі слова (< 25 символів)
    без цифр і без ознак зарплати — це міста.
    """
    if "," not in company:
        return company, ""

    parts = [p.strip() for p in company.split(",")]

    # Шукаємо де закінчується назва і починаються міста
    # Ідемо з кінця: збираємо міста поки вони схожі на міста
    city_parts = []
    company_parts = list(parts)

    while len(company_parts) > 1:
        candidate = company_parts[-1].strip()
        # Місто: коротке, без цифр/валюти, не порожнє
        if (candidate
                and len(candidate) < 25
                and not _SALARY_RE.search(candidate)
                and not candidate.endswith(":")
                and not any(c.isdigit() for c in candidate)):
            city_parts.insert(0, candidate)
            company_parts.pop()
        else:
            break

    return ", ".join(company_parts).strip(), ", ".join(city_parts).strip()


def _parse_dou_title(raw_title: str) -> tuple[str, str, str, str]:
    """Розбирає DOU-формат 'Посада в/на/у Компанія, [Зарплата,] Місто'.
    Повертає (title, company, salary, location).
    """
    sep_idx, separator = -1, None
    for sep in (" в ", " на ", " у "):
        idx = raw_title.find(sep)
        if idx != -1 and (sep_idx == -1 or idx < sep_idx):
            sep_idx, separator = idx, sep

    if sep_idx == -1:
        return raw_title, "", "", ""

    title = raw_title[:sep_idx].strip()
    remainder_after_sep = raw_title[sep_idx + len(separator):]
    parts = [p.strip() for p in remainder_after_sep.split(",")]
    if not parts:
        return title, "", "", ""

    # Якщо весь залишок після роздільника містить маркери опису —
    # одразу пробуємо інший роздільник
    remainder_lower = remainder_after_sep.lower()
    has_desc_marker = any(m in remainder_lower for m in (
        "що потрібний", "ми шукаємо", "займаємося", "цінності",
        "обов'язки", "вимоги", "умови", "пропонуємо",
    ))

    raw_company = remainder_after_sep.split(",")[0]
    # Якщо залишок містить маркери опису — шукаємо наступний роздільник одразу
    company, salary, location = "", "", ""
    if has_desc_marker:
        for sep2 in (" в ", " на ", " у "):
            idx2 = remainder_after_sep.find(sep2)
            if idx2 != -1:
                parts2 = [p.strip() for p in remainder_after_sep[idx2 + len(sep2):].split(",")]
                if parts2 and not _is_invalid_company(parts2[0], title):
                    company = parts2[0]
                    if len(parts2) == 2:
                        if _SALARY_RE.search(parts2[1]):
                            salary = parts2[1].strip()
                        else:
                            location = parts2[1].strip()
                    elif len(parts2) >= 3:
                        if _SALARY_RE.search(parts2[1]):
                            salary = parts2[1].strip()
                            location = ", ".join(parts2[2:]).strip()
                        else:
                            location = parts2[-1].strip()
                    return title, company, salary, location
        return title, "", "", ""

    company, salary, location = parts[0], "", ""
    # Якщо оригінальний parts[0] закінчувався комою в raw рядку — це не назва
    if raw_company.rstrip().endswith(","):
        company = ""

    if len(parts) == 1:
        pass  # тільки company
    elif len(parts) == 2:
        candidate = parts[1].strip()
        if _SALARY_RE.search(candidate):
            salary = candidate
        else:
            location = candidate
    elif len(parts) >= 3:
        # Перевіряємо чи parts[1] — зарплата
        if _SALARY_RE.search(parts[1]):
            salary = parts[1].strip()
            location = ", ".join(parts[2:]).strip()
        else:
            # Все після parts[0] може бути містами або зарплата десь в кінці
            rest = parts[1:]
            # Шукаємо зарплату в rest
            salary_idx = next((i for i, p in enumerate(rest) if _SALARY_RE.search(p)), None)
            if salary_idx is not None:
                salary   = rest[salary_idx].strip()
                location = ", ".join(rest[salary_idx+1:]).strip()
            else:
                # Все rest — міста (або company містить кілька слів)
                location = ", ".join(rest).strip()

    # Очищаємо company від вбудованої зарплати якщо вона там є
    if not salary and _SALARY_RE.search(company):
        sal_match = _SALARY_RE.search(company)
        salary  = sal_match.group(0).strip()
        company = company[:sal_match.start()].strip().rstrip(",").strip()

    # Якщо company містить міста ("Назва, Місто") і location ще порожній
    if not location and "," in company:
        company, location = _split_company_and_cities(company)

    # Якщо company невалідна — пробуємо знайти наступний роздільник у залишку рядка
    if _is_invalid_company(company, title):
        remainder = raw_title[sep_idx + len(separator):]
        for sep2 in (" в ", " на ", " у "):
            idx2 = remainder.find(sep2)
            if idx2 != -1:
                parts2 = [p.strip() for p in remainder[idx2 + len(sep2):].split(",")]
                if parts2 and not _is_invalid_company(parts2[0], title):
                    company = parts2[0]
                    # Перебираємо решту parts2 для salary/location
                    if len(parts2) == 2:
                        if _SALARY_RE.search(parts2[1]):
                            salary = parts2[1].strip()
                        else:
                            location = parts2[1].strip()
                    elif len(parts2) >= 3:
                        if _SALARY_RE.search(parts2[1]):
                            salary = parts2[1].strip()
                            location = ", ".join(parts2[2:]).strip()
                        else:
                            location = parts2[-1].strip()
                    break
        else:
            company = ""

    # Якщо company містить " — " (тире з пробілами) — беремо тільки першу частину
    if " — " in company:
        company = company.split(" — ")[0].strip()
    elif " - " in company and len(company.split(" - ")[0]) < 40:
        company = company.split(" - ")[0].strip()

    return title, company, salary, location


# Словник: форма в тексті → нормальна назва міста
_CITY_FORMS: dict[str, str] = {
    "києві": "Київ", "київ": "Київ",
    "львові": "Львів", "львів": "Львів",
    "одесі": "Одеса", "одеса": "Одеса",
    "дніпрі": "Дніпро", "дніпро": "Дніпро",
    "білій церкві": "Біла Церква", "біла церква": "Біла Церква",
    "вінниці": "Вінниця", "вінниця": "Вінниця",
    "харкові": "Харків", "харків": "Харків",
    "івано-франківську": "Івано-Франківськ", "івано-франківськ": "Івано-Франківськ",
    "тернополі": "Тернопіль", "тернопіль": "Тернопіль",
    "ужгороді": "Ужгород", "ужгород": "Ужгород",
    "луцьку": "Луцьк", "луцьк": "Луцьк",
    "рівному": "Рівне", "рівне": "Рівне",
    "житомирі": "Житомир", "житомир": "Житомир",
    "запоріжжі": "Запоріжжя", "запоріжжя": "Запоріжжя",
    "кривому розі": "Кривий Ріг", "кривий ріг": "Кривий Ріг",
}

# Сортуємо за довжиною (спочатку довші) щоб "кривому розі" перевірялось раніше ніж "кривому"
_CITY_KEYS_SORTED = sorted(_CITY_FORMS.keys(), key=len, reverse=True)


def _extract_location(entry) -> str:
    """Витягує місто з title/description через словник форм назв міст."""
    text = " ".join([
        getattr(entry, "title", ""),
        getattr(entry, "summary", "") or getattr(entry, "description", ""),
    ]).lower()

    for form in _CITY_KEYS_SORTED:
        if form in text:
            return _CITY_FORMS[form]

    return "не знайдено"


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


def _extract_company(entry, title: str = "") -> str:
    """Витягує компанію: спочатку RSS-поля, потім парсинг description.
    Валідує результат через _is_invalid_company.
    """
    # Спочатку пробуємо стандартні RSS-поля
    for field in ("author", "dc_creator", "itunes_author"):
        val = getattr(entry, field, None)
        if val and val.strip() and not _is_invalid_company(val.strip(), title):
            return val.strip()

    # Fallback: парсимо description
    description = getattr(entry, "summary", None) or getattr(entry, "description", "")
    candidate = _extract_company_from_description(description)

    if _is_invalid_company(candidate, title):
        return ""

    # Обрізаємо " — опис" якщо є (наприклад "HostZealot — хостинг, якому довіряють")
    if " — " in candidate:
        candidate = candidate.split(" — ")[0].strip()
    elif " - " in candidate and len(candidate.split(" - ")[0]) < 40:
        candidate = candidate.split(" - ")[0].strip()

    return candidate


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
            company = _extract_company(entry, title)

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
            vacancy_hash=make_vacancy_hash(title, company),
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


def _date_cutoff(days: int | None):
    """Повертає дату cutoff або None якщо без фільтру."""
    if days is None:
        return None
    from datetime import date
    return date.today() - timedelta(days=days)


def _passes_date(v: Vacancy, cutoff) -> bool:
    """Перевіряє чи вакансія проходить фільтр по даті (використовує pub_dt)."""
    if cutoff is None:
        return True

    vdate = None

    # Пріоритет: pub_dt (вже розпарсений datetime object)
    if v.pub_dt is not None:
        try:
            vdate = v.pub_dt.date()
        except Exception:
            pass

    # Fallback: парсимо published рядок (dd.mm.yyyy або RFC 2822)
    if vdate is None:
        pub = v.published
        if pub and pub != "невідомо":
            try:
                from datetime import datetime as _dt
                vdate = _dt.strptime(pub, "%d.%m.%Y").date()
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime as _ptd
                    vdate = _ptd(pub).date()
                except Exception:
                    pass

    if vdate is None:
        print(f"{GREEN}  [DATE] SKIP (невідома дата): {v.title!r}{RESET}")
        return True

    passes = vdate >= cutoff
    if not passes:
        print(f"{GREEN}  [DATE] ВІДФІЛЬТРОВАНО {vdate} < {cutoff}: {v.title!r}{RESET}")
    return passes


def _fetch_raw(days: int | None = None, categories: list[str] | None = None) -> dict[str, list[Vacancy]]:
    """Завантажує всі джерела для обраних категорій, дедуплікує та фільтрує по даті."""
    raw_feeds = build_feeds_for_categories(categories or AVAILABLE_CATEGORIES)
    cutoff = _date_cutoff(days)
    cutoff_str = cutoff.strftime("%d.%m.%Y") if cutoff else "без обмежень"

    raw: dict[str, list[Vacancy]] = {}
    for source_name, urls in raw_feeds.items():
        seen: set[tuple[str, str]] = set()
        vacancies: list[Vacancy] = []
        filtered_out = 0

        for url in urls:
            for v in _fetch_feed(source_name, url):
                if not _passes_date(v, cutoff):
                    filtered_out += 1
                    continue
                if not v.title.strip() or not v.company.strip():
                    vacancies.append(v)
                    continue
                key = _dedup_key(v)
                if key not in seen:
                    seen.add(key)
                    vacancies.append(v)

        print(
            f"{GREEN}[FILTER] {source_name}: "
            f"{len(vacancies)} вак. залишилось | "
            f"відфільтровано по даті: {filtered_out} "
            f"(cutoff: {cutoff_str}){RESET}"
        )
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
    """Об'єднує два списки. При збігу (title, company) — видаляє з secondary.
    Вакансії з порожнім title або company НЕ беруть участь у порівнянні — завжди залишаються.
    """
    seen: set[tuple[str, str]] = {
        _dedup_key(v) for v in primary
        if v.title.strip() and v.company.strip()
    }
    unique_secondary: list[Vacancy] = []
    duplicates = 0

    print(f"{GREEN}\n[MERGE → {name}] primary={len(primary)}, secondary={len(secondary)}{RESET}")
    for v in secondary:
        if not v.title.strip() or not v.company.strip():
            unique_secondary.append(v)
            continue
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

def fetch_all_vacancies(days: int | None = None, categories: list[str] | None = None) -> list[MergedSource]:
    """
    Двоетапне злиття для обраних категорій.
    Кожна категорія → 4 джерела → 1 MergedSource у фіналі.
    """
    active_cats = categories or AVAILABLE_CATEGORIES
    raw = _fetch_raw(days=days, categories=active_cats)

    print(f"{GREEN}\n{'='*60}\nЕТАП 0: Сирі дані\n{'='*60}{RESET}")
    for name, vacancies in raw.items():
        _log_list(name, vacancies)

    merged_sources: list[MergedSource] = []

    for cat in active_cats:
        print(f"{GREEN}\n{'='*60}\nЗЛИТТЯ: {cat}\n{'='*60}{RESET}")

        temp_djinni, _ = _merge(f"Temp Djinni {cat}",
            raw.get(f"Deftech Djinni {cat}", []),
            raw.get(f"Djinni {cat}", []))
        temp_dou, _ = _merge(f"Temp DOU {cat}",
            raw.get(f"Deftech DOU {cat}", []),
            raw.get(f"DOU {cat}", []))

        _log_list(f"Temp Djinni {cat}", temp_djinni)
        _log_list(f"Temp DOU {cat}",    temp_dou)

        final, dups = _merge(f"Final {cat}", temp_djinni, temp_dou)
        _log_list(f"Final {cat}", final)

        merged_sources.append(MergedSource(
            name=f"{cat} (з бронюванням)",
            vacancies=final,
            total_before=len(temp_djinni) + len(temp_dou),
            duplicates=dups,
        ))

    return merged_sources