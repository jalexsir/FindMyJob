"""
RSS-джерела та логіка отримання вакансій.

Завантаження: 8 сирих джерел → двоетапне злиття → 2 фінальних списки.
Фільтрація по датах виконується в bot.py.
"""

import logging
import re as _re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import hashlib
import feedparser
import requests

logger = logging.getLogger(__name__)

# ── Константи ─────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 15
GREEN, RESET = "\033[32m", "\033[0m"

# Детальне логування RSS-запитів/парсингу в консоль (включно з повним дампом
# сирого RSS XML на кожен фід) — вмикай тільки для дебагу, бо на кожен запит
# користувача це друкує в консоль десятки-сотні рядків і сповільнює відповідь.
DEBUG = False

# Логування етапів дедуплікації: списки перед злиттям, які дублікати видалено
# (і скільки) на кожному етапі, фінальний результат. Значно вужче й тихіше за
# DEBUG вище — увімкнено за замовчуванням.
DEBUG_DEDUP = True

def _dprint(*args, **kwargs) -> None:
    if DEBUG:
        print(*args, **kwargs)

def _dedup_print(*args, **kwargs) -> None:
    if DEBUG_DEDUP:
        print(*args, **kwargs)

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
# Список і DOU-слаги — з офіційного <select name="category"> на jobs.dou.ua/vacancies/.
# Djinni-ключове слово — вільнотекстовий пошук (all_keywords); None означає, що
# для цієї категорії Djinni-джерел немає взагалі (перевірено емпірично — або
# нема адекватного відповідника, або ключове слово дає лише шум).

# Найпопулярніші мови програмування — навмисно на початку списку (перші сторінки
# вибору категорій у боті), решта — після них.
AVAILABLE_CATEGORIES = [
    "Python", "Java", "C++", "NET", "PHP", "Node.js", "Golang", "Rust",
    "AI/ML", "Analyst", "Architect", "C-level", "Copywriter",
    "Data Engineer", "Data Science", "Design", "DevOps", "Embedded",
    "Engineering Manager", "ERP/CRM", "Front End", "Hardware",
    "Marketing", "Product Manager", "Project Manager",
    "QA", "SAP", "Security", "Support", "SysAdmin",
    "Technical Writer", "Unity", "Unreal Engine",
]

DOU_CATEGORY_MAP = {
    "NET": ".NET", "AI/ML": "AI/ML", "Analyst": "Analyst", "Architect": "Architect",
    "C++": "C++", "C-level": "C-level", "Copywriter": "Copywriter",
    "Data Engineer": "Data Engineer", "Data Science": "Data Science", "Design": "Design",
    "DevOps": "DevOps", "Embedded": "Embedded", "Engineering Manager": "Engineering Manager",
    "ERP/CRM": "ERP/CRM", "Front End": "Front End", "Golang": "Golang", "Hardware": "Hardware",
    "Java": "Java", "Marketing": "Marketing", "Node.js": "Node.js", "PHP": "PHP",
    "Product Manager": "Product Manager", "Project Manager": "Project Manager",
    "Python": "Python", "QA": "QA", "Rust": "Rust", "SAP": "SAP", "Security": "Security",
    "Support": "Support", "SysAdmin": "SysAdmin", "Technical Writer": "Technical Writer",
    "Unity": "Unity", "Unreal Engine": "Unreal Engine",
}

DJINNI_KEYWORD_MAP = {
    "NET": ".net", "AI/ML": "ai/ml", "Analyst": "analyst", "Architect": "architect",
    "C++": "c++", "C-level": None, "Copywriter": "copywriter",
    "Data Engineer": "data engineer", "Data Science": "data science", "Design": "design",
    "DevOps": "devops", "Embedded": "embedded", "Engineering Manager": "engineering manager",
    "ERP/CRM": "erp", "Front End": "front end", "Golang": "golang", "Hardware": "hardware",
    "Java": "java", "Marketing": "marketing", "Node.js": "node.js", "PHP": "php",
    "Product Manager": "product manager", "Project Manager": "project manager",
    "Python": "python", "QA": "qa", "Rust": "rust", "SAP": "sap", "Security": "security",
    "Support": "support", "SysAdmin": "sysadmin", "Technical Writer": "technical writer",
    "Unity": "unity", "Unreal Engine": "unreal engine",
}


# Назви джерел (використовуються і як ключі внутрішніх словників, і як значення
# Vacancy.source — тобто те, що показується у полі "Фільтр" під кожною вакансією).
def _dou_deftech_name(cat: str) -> str:
    return f"DOU Deftech {cat}"

def _dou_regular_name(cat: str) -> str:
    return f"DOU (бронювання) {cat}"

def _djinni_deftech_name(cat: str) -> str:
    return f"Djinni Deftech {cat}"

def _djinni_regular_name(cat: str) -> str:
    return f"Djinni (бронювання) {cat}"


def build_feeds_for_categories(categories: list[str]) -> dict[str, list[str]]:
    """Будує RAW_FEEDS динамічно для обраних категорій.
    Якщо для категорії немає Djinni-ключа (DJINNI_KEYWORD_MAP -> None, напр.
    "C-level") — Djinni-джерела для неї просто не додаються."""
    feeds: dict[str, list[str]] = {}
    for cat in categories:
        dou_cat   = DOU_CATEGORY_MAP.get(cat, cat)
        djinni_kw = DJINNI_KEYWORD_MAP.get(cat, cat.lower())
        feeds[_dou_deftech_name(cat)] = [f"https://jobs.dou.ua/vacancies/feeds/?category={dou_cat}&search=miltech"]
        feeds[_dou_regular_name(cat)] = [f"https://jobs.dou.ua/vacancies/feeds/?category={dou_cat}&search={quote('бронювання')}"]
        if djinni_kw:
            feeds[_djinni_deftech_name(cat)] = [f"https://djinni.co/jobs/rss/?all_keywords={quote(djinni_kw)}&search_type=basic-search&editorial=miltech"]
            feeds[_djinni_regular_name(cat)] = [f"https://djinni.co/jobs/rss/?all_keywords={quote(djinni_kw)}&search_type=basic-search&editorial=reservation"]
    return feeds


# ── Моделі ────────────────────────────────────────────────────────────────────

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
    зливались в один "short_link" і приховування однієї ховало й іншу."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


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
    category: str = ""      # напр. "Java" — заповнюється в fetch_all_vacancies після злиття


@dataclass
class MergedSource:
    """Фінальний список вакансій після злиття та дедуплікації."""
    name: str
    vacancies: list[Vacancy]
    total_before: int
    duplicates: int


# ── Парсери ───────────────────────────────────────────────────────────────────

_HOMOGLYPH_MAP = str.maketrans({
    "С": "C", "с": "c", "Е": "E", "е": "e", "О": "O", "о": "o",
    "Р": "P", "р": "p", "Х": "X", "х": "x", "А": "A", "а": "a",
    "В": "B", "в": "b", "Н": "H", "н": "h", "К": "K", "к": "k",
    "М": "M", "м": "m", "Т": "T", "т": "t",
})

def _normalize_homoglyphs(s: str) -> str:
    """Замінює кириличні літери-двійники (С→C, Е→E тощо) на латинські для порівняння."""
    return s.translate(_HOMOGLYPH_MAP)


def _is_invalid_company(text: str, title: str = "") -> bool:
    """Повертає True якщо текст НЕ є назвою компанії."""
    if not text:
        return True
    # Нормалізуємо nbsp (\xa0) до звичайного пробілу та типографські апострофи
    t = text.strip().replace("\xa0", " ").replace("\u2019", "'").replace("\u2018", "'")
    t = _re.sub(r"\s+", " ", t).strip()
    # Закінчується на двокрапку, кому, крапку або знак питання — заголовок/речення
    if t.endswith((":", ",", ".", "?", "!")):
        return True
    # Довше 60 символів — скоріш за все опис
    if len(t) > 60:
        return True
    # Більше 5 слів — швидше речення, ніж назва компанії
    if len(t.split()) > 5:
        return True
    # Містить "/" — швидше перелік технологій/посад, ніж назва компанії
    # (напр. "Hardware, Software та MilTech/IoT")
    if "/" in t:
        return True
    # Перше слово — типовий службовий/функціональний маркер (не буває на початку
    # реальної назви компанії): займенники, сполучники, заголовки секцій тощо
    GENERIC_LEADING_WORDS = {
        "можливість", "де", "коли", "яка", "який", "яке", "які",
        "що", "як", "чим", "і", "та", "а", "чи", "або", "не", "це", "хто",
        "ваша", "ваш", "ваші", "наш", "наша", "наші", "наший",
        "у", "в",  # "у нас"/"в нас вся команда..." — початок опису, не назва
        "основні", "необхідні", "вимоги", "обов'язки",
        "команда", "команди", "компанія", "компанії", "клієнта", "клієнт",
        "core", "what", "responsibilities", "requirements", "qualifications",
        "about", "your", "level", "focus", "location", "sensor", "ideal",
        "nice", "tools", "sensor & platform",
        "this", "that", "it", "he", "she", "they", "we", "you", "i",
        "надсилайте", "надсилай", "надішли",
    }
    # Кандидат — просто число + слово (наприклад "5 роки", "3 years") — не назва
    if _re.match(r'^\d+[\+]?\s*(?:рок|рік|year|міс)', t.lower()):
        return True
    first_word = t.lower().split()[0].strip("'\"()") if t.split() else ""
    if first_word in GENERIC_LEADING_WORDS:
        return True
    # Збігається з назвою посади (title дублюється) — порівнюємо з урахуванням гомогліфів
    if title:
        t_norm = _normalize_homoglyphs(t).lower()
        title_norm = _normalize_homoglyphs(title).lower()
        if t_norm == title_norm:
            return True
        # Перші 2 слова company збігаються з першими 2 словами title — дублікат
        t_words = t_norm.split()[:2]
        ti_words = title_norm.split()[:2]
        if t_words and t_words == ti_words:
            return True
        # Узагальнена перевірка перекриття значущих слів (>=3 символи) з title —
        # ловить дублікати типу "Senior CRM & Workflow Administrator" при
        # title="CRM / Workflow Administrator"
        strip_punct = lambda w: _re.sub(r"[^\w]", "", w)
        t_sig = {strip_punct(w) for w in t_norm.split() if len(strip_punct(w)) >= 3}
        ti_sig = {strip_punct(w) for w in title_norm.split() if len(strip_punct(w)) >= 3}
        t_sig.discard("")
        ti_sig.discard("")
        if t_sig and ti_sig:
            overlap = t_sig & ti_sig
            smaller = min(len(t_sig), len(ti_sig))
            if smaller and len(overlap) / smaller >= 0.6:
                return True
    # Типові заголовки секцій вакансій та загальні іменники (точний збіг)
    SECTION_HEADERS = {
        "огляд", "опис", "опис вакансії", "про вакансію", "про проект",
        "про проєкт", "про компанію", "про нас", "про роль", "деталі",
        "вимоги", "обов'язки", "умови", "переваги", "бенефіти",
        "компанія", "компанії", "команда", "команди", "клієнта", "клієнт",
        "клієнти", "проєкт", "проєкту", "продукт", "продукту",
        "бізнес", "бізнесу", "де команда", "ваша роль",
        "overview", "description", "about us", "about the role",
        "about the team", "your role", "your mission",
        "core technical skills", "sensor & platform experience",
        "ideal candidate profile", "nice to have",
        "tools & technologies you'll work with", "what you'll do",
        "what we're looking for", "requirements", "responsibilities",
        "benefits", "what we offer", "qualifications",
    }
    if t.lower() in SECTION_HEADERS:
        return True
    # Типові слова що вказують на опис а не назву компанії
    desc_markers = (
        "ми шукаємо", "що потрібний", "зсу в ", "займаємося", "цінності",
        "обов'язки", "вимоги", "умови", "пропонуємо",
        "architect the", "management:", "stack &", "tool management",
        "якому довіряють", "якій довіряють", "очікуємо від тебе",
    )
    tl = t.lower()
    if any(m in tl for m in desc_markers):
        return True
    return False


def _is_invalid_dou_company(text: str, title: str = "") -> bool:
    """Легка валідація company для DOU title-парсингу.
    Без обмеження кількості слів — офіційні назви військових частин
    можуть бути довгими ('414 окрема бригада безпілотних систем «Птахи Мадяра»').
    """
    if not text:
        return True
    t = text.strip().replace("\u2019", "'").replace("\u2018", "'")
    if t.endswith(":") or t.endswith(",") or t.endswith("."):
        return True
    if len(t) > 100:
        return True
    if title and t.lower() == title.lower():
        return True
    # Якщо перші 2 слова company збігаються з першими 2 словами title — дублікат
    if title:
        t_words = t.lower().split()[:2]
        ti_words = title.lower().split()[:2]
        if t_words and t_words == ti_words:
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
                if parts2 and not _is_invalid_dou_company(parts2[0], title):
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
    if _is_invalid_dou_company(company, title):
        remainder = raw_title[sep_idx + len(separator):]
        for sep2 in (" в ", " на ", " у "):
            idx2 = remainder.find(sep2)
            if idx2 != -1:
                parts2 = [p.strip() for p in remainder[idx2 + len(sep2):].split(",")]
                if parts2 and not _is_invalid_dou_company(parts2[0], title):
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


def _extract_location(entry, source_name: str = "") -> str:
    """Витягує місто з title/description через словник форм назв міст.
    Також шукає 'формат роботи' для визначення віддаленої роботи.

    Парсинг description застосовується ТІЛЬКИ для Djinni-джерел —
    для DOU локація вже витягується з <title> через _parse_dou_title.
    """
    if source_name and "djinni" not in source_name.lower():
        return "не знайдено"

    import html as _html

    raw = " ".join([
        getattr(entry, "title", ""),
        getattr(entry, "summary", "") or getattr(entry, "description", ""),
    ])
    text = _html.unescape(_html.unescape(raw)).lower()

    if _re.search(r'(?:формат роботи[:\s—-]+віддален|віддален\w*\s+формат роботи)', text):
        return "Віддалено"

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


def vacancy_sort_date(v: "Vacancy"):
    """Дата публікації для сортування списку (найстаріші — на початок).
    Пріоритет: pub_dt, потім парсинг published-рядка (dd.mm.yyyy або RFC 2822).
    Повертає None, якщо дату визначити не вдалося — такі вакансії сортуються в кінець."""
    if v.pub_dt is not None:
        try:
            return v.pub_dt.date()
        except Exception:
            pass
    pub = v.published
    if pub and pub != "невідомо":
        try:
            return datetime.strptime(pub, "%d.%m.%Y").date()
        except ValueError:
            try:
                return parsedate_to_datetime(pub).date()
            except Exception:
                pass
    return None


# Патерн для латинських слів (назва компанії)
_LATIN_RE = _re.compile(r'[A-Za-z]')

def _extract_company_from_description(description: str) -> str:
    """
    Витягує назву компанії з HTML-опису вакансії.

    Стратегії (в порядку пріоритету):
    1. "Назва розробляє/шукає/займається..." — найнадійніший патерн
    2. Перший ВАЛІДНИЙ <strong>Назва</strong> (пропускає заголовки типу "Чим ми займаємося:")
    3. Текст після <p> до " -" або " —" — якщо містить латиницю
    """
    import html as _html

    if not description:
        return ""

    text = _html.unescape(_html.unescape(description))

    # Стратегія 1: "Назва дієслово" — компанія перед типовим дієсловом-маркером.
    # Обробляємо КОЖЕН <p> окремо, щоб не захоплювати текст із сусідніх параграфів.
    ACTION_VERBS = (
        r'розробляє', r'займається', r'шукає', r'запрошує',
        r'надає', r'створює', r'є лідером',
        r'є компанією', r'спеціалізується', r'працює',
    )
    STOP_WORDS = {
        "ми", "вони", "він", "вона", "я", "ти", "це",
        "наша", "наш", "наші", "що", "як", "чим", "хто", "де", "коли",
        "що ми", "як ми", "чим ми",
        "яка", "який", "яке", "які", "котра", "котрий", "котре", "котрі",
        "команда", "команди", "компанія", "компанії", "клієнта", "клієнт",
        "проєкт", "проєкту", "продукт", "продукту",
    }
    verb_pattern = r'([A-Za-zА-ЯҐЄІЇа-яґєії0-9][A-Za-zА-ЯҐЄІЇа-яґєії0-9\s\-\.&]{0,50}?)\s+(?:' + '|'.join(ACTION_VERBS) + r')\b'

    paragraphs = _re.findall(r'<p[^>]*>(.*?)</p>', text, _re.IGNORECASE | _re.DOTALL) or [text]
    for para in paragraphs:
        para_plain = _re.sub(r'<[^>]+>', ' ', para)
        para_plain = _html.unescape(para_plain).strip()

        for verb_match in _re.finditer(verb_pattern, para_plain, _re.IGNORECASE):
            candidate = verb_match.group(1).strip().rstrip(',')
            parts = _re.split(r'[\n\.\!:]', candidate)
            candidate = parts[-1].strip() if parts else candidate
            cand_lower = candidate.lower()
            words = cand_lower.split()
            if all(w in STOP_WORDS for w in words):
                continue
            if (candidate
                    and len(candidate) < 60
                    and cand_lower not in STOP_WORDS
                    and not _is_invalid_company(candidate)):
                return candidate

    # Стратегія 2 (позитивний патерн): "Назва — опис" або "Назва is a/an/the ..."
    # ЛИШЕ на початку абзацу, і ЛИШЕ серед перших 3 абзаців опису — вступ
    # про компанію завжди йде на самому початку. Якщо шукати по всьому тексту,
    # той самий патерн (тире/дієслово "is") трапляється в будь-якому реченні
    # десь всередині опису і дає хибні спрацювання.
    intro_pattern = _re.compile(
        r'^\s*(?:<strong[^>]*>\s*)?'
        r'([A-Za-zА-ЯҐЄІЇа-яґєії0-9][^<]{1,58}?)'
        r'\s*(?:</strong>\s*)?'
        r'(?:[-—]\s|is\s+an?\s+|is\s+the\s+)',
        _re.IGNORECASE,
    )
    for para in paragraphs[:3]:
        m = intro_pattern.match(para)
        if not m:
            continue
        candidate = _re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if candidate and not _is_invalid_company(candidate):
            return candidate

    return ""


def _extract_company(entry, title: str = "", source_name: str = "") -> str:
    """Витягує компанію: спочатку RSS-поля, потім парсинг description.

    Парсинг <description> застосовується ТІЛЬКИ для Djinni-джерел
    (Deftech Djinni, Djinni) — у DOU вся інформація вже в <title>,
    і парсинг description там дає хибні спрацювання.
    """
    # Спочатку пробуємо стандартні RSS-поля (працює для обох типів джерел)
    for field in ("author", "dc_creator", "itunes_author"):
        val = getattr(entry, field, None)
        if val and val.strip() and not _is_invalid_company(val.strip(), title):
            return val.strip()

    # Fallback на опис — тільки для Djinni
    if "djinni" not in source_name.lower():
        return ""

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
        _dprint(f"{GREEN}[{source_name}] ❌ ПОМИЛКА: {exc}{RESET}")
        return []

    raw_text = response.content.decode("utf-8", errors="replace")
    _dprint(
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
            company = _extract_company(entry, title, source_name)
            if not company:
                raw_desc = getattr(entry, "summary", None) or getattr(entry, "description", "")
                _dprint(f"{GREEN}  [NO COMPANY] title={title!r}")
                _dprint(f"    description raw (перші 300): {repr(raw_desc[:300])}{RESET}")

        location = loc_from_title or _extract_location(entry, source_name)
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

    _dprint(f"{GREEN}[{source_name}] Записів у RSS: {len(parsed.entries)} | Завантажено: {len(vacancies)}{RESET}")
    if parsed.entries:
        e0 = parsed.entries[0]
        company_fields = {f: getattr(e0, f, None) for f in ("author", "dc_creator", "itunes_author")}
        _dprint(f"{GREEN}  Всі title ({len(parsed.entries)}):{RESET}")
        for e in parsed.entries:
            _dprint(f"{GREEN}    → {getattr(e, 'title', '???')}{RESET}")
        _dprint(f"{GREEN}  Поля компанії 1-го запису: {company_fields}{RESET}")

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
                vdate = datetime.strptime(pub, "%d.%m.%Y").date()
            except ValueError:
                try:
                    vdate = parsedate_to_datetime(pub).date()
                except Exception:
                    pass

    if vdate is None:
        _dprint(f"{GREEN}  [DATE] SKIP (невідома дата): {v.title!r}{RESET}")
        return True

    passes = vdate >= cutoff
    if not passes:
        _dprint(f"{GREEN}  [DATE] ВІДФІЛЬТРОВАНО {vdate} < {cutoff}: {v.title!r}{RESET}")
    return passes


def _fetch_raw(categories: list[str] | None = None) -> tuple[dict[str, list[Vacancy]], dict[str, int]]:
    """Завантажує всі джерела ПАРАЛЕЛЬНО (кожен фід — окремий мережевий запит,
    тож послідовно вони можуть коштувати до REQUEST_TIMEOUT секунд кожен).
    Без фільтру по даті — той самий "всечасовий" знімок далі ділять між собою
    всі часові вікна (1 день / 7 днів / весь час), див. fetch_all_vacancies.
    Повертає (raw, dups_per_source)."""
    raw_feeds = build_feeds_for_categories(categories or AVAILABLE_CATEGORIES)

    fetch_jobs = [(name, url) for name, urls in raw_feeds.items() for url in urls]
    fetched: dict[str, list[Vacancy]] = {name: [] for name in raw_feeds}

    if fetch_jobs:
        with ThreadPoolExecutor(max_workers=len(fetch_jobs)) as executor:
            future_to_name = {
                executor.submit(_fetch_feed, name, url): name
                for name, url in fetch_jobs
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                fetched[name].extend(future.result())

    raw: dict[str, list[Vacancy]] = {}
    dups_per_source: dict[str, int] = {}

    for source_name, entries in fetched.items():
        # Всередині ОДНОГО сирого джерела дублікат — це буквально той самий URL
        # (той самий запис міг прийти в RSS двічі); порівнюємо за посиланням.
        seen_links: dict[str, Vacancy] = {}
        vacancies: list[Vacancy] = []
        inner_dups = 0

        for v in entries:
            if not v.link:
                vacancies.append(v)
                continue
            original = seen_links.get(v.link)
            if original is None:
                seen_links[v.link] = v
                vacancies.append(v)
            else:
                # Пари дублікатів тут НЕ логуємо навмисно — детальну інформацію
                # про дублікати друкуємо тільки для фінального злиття в Final.
                inner_dups += 1

        _dedup_print(
            f"{GREEN}[FILTER] {source_name}: "
            f"{len(vacancies)} вак. | "
            f"дублікатів всередині: {inner_dups}{RESET}"
        )
        raw[source_name] = vacancies
        dups_per_source[source_name] = inner_dups

    return raw, dups_per_source


# ── Логування ─────────────────────────────────────────────────────────────────

def _log_list(name: str, vacancies: list[Vacancy]) -> None:
    _dedup_print(f"{GREEN}\n{'─'*60}\n📋 {name} ({len(vacancies)} вак.):{RESET}")
    for i, v in enumerate(vacancies, 1):
        _dedup_print(f"{GREEN}  {i:2}. title={v.title!r}, company={v.company!r}{RESET}")
    if not vacancies:
        _dedup_print(f"{GREEN}  (порожній){RESET}")


def _log_duplicate_pair(context_label: str, kept: Vacancy, removed: Vacancy) -> None:
    """Логує ОБИДВІ вакансії пари-дубліката (з посиланнями) ще до видалення."""
    _dedup_print(
        f"{GREEN}  ✂ ДУБЛІКАТ {context_label}:\n"
        f"      залишено: title={kept.title!r}, company={kept.company!r}, link={kept.link}\n"
        f"      видалено: title={removed.title!r}, company={removed.company!r}, link={removed.link}"
        f"{RESET}"
    )


# ── Злиття з дедуплікацією ────────────────────────────────────────────────────


def _merge(name: str, primary: list[Vacancy], secondary: list[Vacancy], by: str = "title_company") -> tuple[list[Vacancy], int]:
    """Об'єднує два списки. При збігу — видаляє з secondary.

    by="link" — дублікат визначається за ПОВНИМ посиланням. Для злиття
        Deftech-варіанту зі "звичайним" В МЕЖАХ ОДНОГО САЙТУ (Deftech DOU + DOU,
        Deftech Djinni + Djinni) — там та сама вакансія має буквально той самий URL.

    by="title_company" (за замовчуванням) — якщо є і title, і company —
        порівнюємо по парі; якщо company порожній — тільки по title. Для
        фінального злиття DOU з Djinni — це РІЗНІ сайти з різними URL, тож
        навіть та сама вакансія матиме різні посилання, порівнювати їх марно.
    """
    _dedup_print(f"{GREEN}\n[MERGE → {name}] primary={len(primary)}, secondary={len(secondary)}{RESET}")

    if by == "link":
        # Пари дублікатів тут НЕ логуємо навмисно — детальну інформацію про
        # дублікати (title/company/link) друкуємо тільки для фінального злиття
        # в Final (див. нижче, by="title_company"), щоб не дублювати шум.
        seen_links: dict[str, Vacancy] = {v.link: v for v in primary if v.link}
        unique_secondary: list[Vacancy] = []
        duplicates = 0
        for v in secondary:
            original = seen_links.get(v.link) if v.link else None
            if original is not None:
                duplicates += 1
            else:
                if v.link:
                    seen_links[v.link] = v
                unique_secondary.append(v)
        combined = primary + unique_secondary
        _dedup_print(f"{GREEN}  ✅ {len(combined)} вак. (видалено {duplicates}){RESET}")
        return combined, duplicates

    # by == "title_company" — існуючі правила, без змін.
    # Будуємо seen з primary. Значення — сама вакансія (не просто ключ), щоб при
    # виявленні дубліката залогувати ОБИДВІ вакансії разом з посиланнями.
    seen_full: dict[tuple[str, str], Vacancy] = {}   # (title, company) де company не порожній
    seen_full_titles: dict[str, Vacancy] = {}          # титули з seen_full — для O(1) look-up
    seen_title: dict[str, Vacancy] = {}               # тільки title де company порожній

    for v in primary:
        t = v.title.strip().lower()
        c = v.company.strip().lower()
        if t and c:
            seen_full[(t, c)] = v
            seen_full_titles[t] = v
        elif t:
            seen_title[t] = v

    unique_secondary = []
    duplicates = 0

    for v in secondary:
        t = v.title.strip().lower()
        c = v.company.strip().lower()

        if not t:
            # Порожній title — завжди залишаємо
            unique_secondary.append(v)
            continue

        original: Vacancy | None = None
        if c:
            # Є і title і company — порівнюємо по парі
            if (t, c) in seen_full:
                original = seen_full[(t, c)]
            else:
                seen_full[(t, c)] = v
                seen_full_titles[t] = v
        else:
            # company порожній — порівнюємо тільки по title
            original = seen_title.get(t) or seen_full_titles.get(t)
            if original is None:
                seen_title[t] = v

        if original is not None:
            _log_duplicate_pair(f"У {name}", original, v)
            duplicates += 1
        else:
            unique_secondary.append(v)

    combined = primary + unique_secondary
    _dedup_print(f"{GREEN}  ✅ {len(combined)} вак. (видалено {duplicates}){RESET}")
    return combined, duplicates


# ── Публічний API ─────────────────────────────────────────────────────────────
# ── Кеш результатів фетчу ─────────────────────────────────────────────────────
# Клік по кнопці меню ("Всі вакансії" тощо) раніше завжди означав повний ре-фетч
# усіх RSS-джерел з нуля. TTL-кеш дозволяє повторним запитам (той самий набір
# категорій) у межах CACHE_TTL_SECONDS віддавати вже готовий результат Етапу 1
# (без фільтру по даті — сам фільтр і Етап 2 рахуються щоразу наново, дешево,
# без мережі, див. fetch_all_vacancies).
CACHE_TTL_SECONDS = 120
_cache_lock = threading.Lock()
_vacancies_cache: dict[tuple, tuple[float, dict[str, dict[str, list["Vacancy"]]]]] = {}


def fetch_all_vacancies(days: int | None = None, categories: list[str] | None = None) -> list[MergedSource]:
    """Двоетапне злиття для обраних категорій (з TTL-кешем, див. CACHE_TTL_SECONDS).

    ВАЖЛИВО щодо порядку операцій (щоб "видалено N дублікатів" ніколи не
    перевищувало кількість показаних вакансій):
      1. Кешується/фетчиться лише ЕТАП 1 (внутрішньосайтове злиття за
         посиланням, до Temp Djinni/Temp DOU) — БЕЗ фільтру по даті, тож усі
         часові вікна (1 день/7 днів/весь час) ділять один і той самий знімок фіду.
      2. Temp Djinni/Temp DOU обрізаються по даті (для конкретного запиту).
      3. І ЛИШЕ ПОТІМ — Етап 2 (фінальне злиття DOU+Djinni за title/company) —
         на вже дато-обрізаних списках. Кількість видалених дублікатів рахується
         саме тут, тож вона завжди узгоджена з тим, що реально показано.
    """
    active_cats = categories or AVAILABLE_CATEGORIES
    cache_key = tuple(sorted(active_cats))

    with _cache_lock:
        cached = _vacancies_cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < CACHE_TTL_SECONDS:
            stage1 = cached[1]
            from_cache = True
        else:
            stage1 = None
            from_cache = False

    if stage1 is None:
        stage1 = _fetch_all_vacancies_uncached(active_cats)
        with _cache_lock:
            _vacancies_cache[cache_key] = (time.monotonic(), stage1)

    period = f"останні {days} д." if days is not None else "весь час"
    tag = "з кешу" if from_cache else "живий фетч"
    _dedup_print(f"{GREEN}\n{'='*60}\nЕТАП 2 ({tag}, {period})\n{'='*60}{RESET}")

    cutoff = _date_cutoff(days)
    result: list[MergedSource] = []

    for cat in active_cats:
        entry = stage1.get(cat, {"temp_djinni": [], "temp_dou": []})
        if days is None:
            temp_djinni, temp_dou = entry["temp_djinni"], entry["temp_dou"]
        else:
            temp_djinni = [v for v in entry["temp_djinni"] if _passes_date(v, cutoff)]
            temp_dou    = [v for v in entry["temp_dou"] if _passes_date(v, cutoff)]

        # DOU primary (пріоритет), Djinni secondary — різні сайти, різні URL
        # навіть для тієї самої вакансії, тож порівнюємо за (title, company).
        final, duplicates = _merge(f"Final {cat}", temp_dou, temp_djinni)
        _log_list(f"Final {cat}", final)

        result.append(MergedSource(
            name=f"{cat} (з бронюванням)",
            vacancies=final,
            total_before=len(temp_djinni) + len(temp_dou),
            duplicates=duplicates,
        ))

    return result


def _fetch_all_vacancies_uncached(active_cats: list[str]) -> dict[str, dict[str, list[Vacancy]]]:
    """Етап 0 (сирі дані) + Етап 1 (внутрішньосайтове злиття за посиланням).
    Повертає {cat: {"temp_djinni": [...], "temp_dou": [...]}} — БЕЗ фільтру по
    даті й БЕЗ фінального злиття (Етап 2 робиться пізніше, вже на льоту, в
    fetch_all_vacancies — після обрізання по даті)."""
    raw, dups_per_source = _fetch_raw(categories=active_cats)

    _dedup_print(f"{GREEN}\n{'='*60}\nЕТАП 0: Сирі дані\n{'='*60}{RESET}")
    for name, vacancies in raw.items():
        _log_list(name, vacancies)

    stage1: dict[str, dict[str, list[Vacancy]]] = {}

    for cat in active_cats:
        _dedup_print(f"{GREEN}\n{'='*60}\nЗЛИТТЯ (Етап 1): {cat}\n{'='*60}{RESET}")

        # У межах ОДНОГО сайту (Deftech-варіант + звичайний) — дублікат це той
        # самий URL, порівнюємо за посиланням.
        temp_djinni, _ = _merge(f"Temp Djinni {cat}",
            raw.get(_djinni_deftech_name(cat), []),
            raw.get(_djinni_regular_name(cat), []), by="link")
        temp_dou, _ = _merge(f"Temp DOU {cat}",
            raw.get(_dou_deftech_name(cat), []),
            raw.get(_dou_regular_name(cat), []), by="link")

        for v in temp_djinni:
            v.category = cat
        for v in temp_dou:
            v.category = cat

        _log_list(f"Temp Djinni {cat}", temp_djinni)
        _log_list(f"Temp DOU {cat}",    temp_dou)

        stage1[cat] = {"temp_djinni": temp_djinni, "temp_dou": temp_dou}

    return stage1