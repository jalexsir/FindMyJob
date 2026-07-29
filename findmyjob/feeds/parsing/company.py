"""Розпізнавання назви компанії та відсіювання хибних кандидатів.

RSS-фіди Djinni не мають окремого поля з компанією — її доводиться діставати з
HTML-опису. Тому основна складність тут не «витягти», а «зрозуміти, що витягли
не те»: заголовок секції, перше речення опису або дубль назви посади.
"""

from __future__ import annotations

import html
import re

from .text import (
    collapse_spaces, normalize_apostrophes, normalize_homoglyphs,
    strip_tags, unescape_twice,
)

# ── Словники хибних кандидатів ────────────────────────────────────────────────

# Перше слово — типовий службовий/функціональний маркер (не буває на початку
# реальної назви компанії): займенники, сполучники, заголовки секцій тощо.
GENERIC_LEADING_WORDS = {
    "привіт", "вітаю", "вітаємо", "вже",  # "Привіт! Ми — …", "вже 25 років …"
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

# Типові заголовки секцій вакансій та загальні іменники (точний збіг).
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

# Слова-маркери, що вказують на опис, а не назву компанії.
DESCRIPTION_MARKERS = (
    "ми шукаємо", "що потрібний", "зсу в ", "займаємося", "цінності",
    "обов'язки", "вимоги", "умови", "пропонуємо",
    "architect the", "management:", "stack &", "tool management",
    "якому довіряють", "якій довіряють", "очікуємо від тебе",
)

# Дієслова-маркери: "<Назва> розробляє/шукає/…" — найнадійніший патерн.
_ACTION_VERBS = (
    "розробляє", "займається", "шукає", "запрошує",
    "надає", "створює", "є лідером",
    "є компанією", "спеціалізується", "працює",
)

# Займенники/загальні іменники, що можуть стояти перед дієсловом-маркером
# замість назви компанії ("Ми розробляємо…").
_SUBJECT_STOP_WORDS = {
    "ми", "вони", "він", "вона", "я", "ти", "це",
    "наша", "наш", "наші", "що", "як", "чим", "хто", "де", "коли",
    "що ми", "як ми", "чим ми",
    "яка", "який", "яке", "які", "котра", "котрий", "котре", "котрі",
    "команда", "команди", "компанія", "компанії", "клієнта", "клієнт",
    "проєкт", "проєкту", "продукт", "продукту",
}

# Лапки в класі символів навмисно: без них у «Група компаній «Промавтоматика»
# вже 25 років працює» збіг починався б після закривальної лапки й давав
# «вже 25 років». З ними назва потрапляє в кандидата цілком, а `_quoted_name`
# лишає з нього саме те, що в лапках.
_VERB_RE = re.compile(
    r"([A-Za-zА-ЯҐЄІЇа-яґєії0-9«»\"“”„][A-Za-zА-ЯҐЄІЇа-яґєії0-9\s\-\.&«»\"“”„]{0,50}?)"
    r"\s+(?:" + "|".join(_ACTION_VERBS) + r")\b",
    re.IGNORECASE,
)

# Назва в лапках — найнадійніший сигнал: «Промавтоматика», "Acme Inc".
_QUOTED_RE = re.compile(r"[«\"“„]\s*([^«»\"“”„]{2,60}?)\s*[»\"”]")

# Привітання на початку опису: назва компанії стоїть праворуч від тире.
_GREETINGS = {"привіт", "вітаю", "вітаємо", "hi", "hello", "hey"}
_TRIM_CHARS = "'\"()!?,.:;«»„“”…-–—"

# "Назва — опис" або "Назва is a/an/the ...". ЛИШЕ на початку абзацу і ЛИШЕ серед
# перших абзаців: вступ про компанію завжди йде на самому початку, а по всьому
# тексту той самий патерн трапляється в будь-якому реченні і дає хибні збіги.
_INTRO_RE = re.compile(
    r"^\s*(?:<strong[^>]*>\s*)?"
    r"([A-Za-zА-ЯҐЄІЇа-яґєії0-9][^<]{1,58}?)"
    r"\s*(?:</strong>\s*)?"
    r"(?:[-—]\s|is\s+an?\s+|is\s+the\s+)",
    re.IGNORECASE,
)
_INTRO_PARAGRAPH_LIMIT = 3

_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
# Видаляє теги БЕЗ пробілу на їх місці — на відміну від text.strip_tags().
_TAG_ONLY_RE = re.compile(r"<[^>]+>")
_YEARS_RE = re.compile(r"^\d+[\+]?\s*(?:рок|рік|year|міс)")
_NON_WORD_RE = re.compile(r"[^\w]")

MAX_COMPANY_LENGTH = 60
MAX_DOU_COMPANY_LENGTH = 100
MAX_COMPANY_WORDS = 5
TITLE_OVERLAP_THRESHOLD = 0.6


# ── Валідація ─────────────────────────────────────────────────────────────────

def is_invalid_company(text: str, title: str = "") -> bool:
    """Повертає True якщо текст НЕ є назвою компанії."""
    if not text:
        return True

    candidate = collapse_spaces(
        normalize_apostrophes(text.strip().replace("\xa0", " "))
    )

    # Закінчується на розділовий знак — заголовок/речення, не назва
    if candidate.endswith((":", ",", ".", "?", "!")):
        return True
    # Довше 60 символів — скоріш за все опис
    if len(candidate) > MAX_COMPANY_LENGTH:
        return True
    # Більше 5 слів — швидше речення, ніж назва компанії
    if len(candidate.split()) > MAX_COMPANY_WORDS:
        return True
    # Містить "/" — швидше перелік технологій/посад
    # (напр. "Hardware, Software та MilTech/IoT")
    if "/" in candidate:
        return True
    # Просто число + слово ("5 роки", "3 years") — не назва
    if _YEARS_RE.match(candidate.lower()):
        return True

    words = candidate.split()
    first_word = words[0].lower().strip(_TRIM_CHARS) if words else ""
    if first_word in GENERIC_LEADING_WORDS:
        return True

    # Самі лише займенники/загальні іменники ("Ми", "Наша команда") — це початок
    # опису, а не назва. Djinni-описи часто відкриваються "Ми — команда, яка…",
    # і патерн "<Назва> — опис" сумлінно віддає звідти "Ми".
    if all(word.lower().strip(_TRIM_CHARS) in _SUBJECT_STOP_WORDS for word in words):
        return True

    if title and _duplicates_title(candidate, title):
        return True

    if candidate.lower() in SECTION_HEADERS:
        return True

    lowered = candidate.lower()
    return any(marker in lowered for marker in DESCRIPTION_MARKERS)


def _duplicates_title(candidate: str, title: str) -> bool:
    """Чи кандидат — це просто повтор назви посади (з поправкою на гомогліфи)."""
    candidate_norm = normalize_homoglyphs(candidate).lower()
    title_norm = normalize_homoglyphs(title).lower()

    if candidate_norm == title_norm:
        return True

    # Перші 2 слова збігаються — дублікат
    if candidate_norm.split()[:2] and candidate_norm.split()[:2] == title_norm.split()[:2]:
        return True

    # Узагальнена перевірка перекриття значущих слів (>=3 символи) — ловить
    # дублікати типу "Senior CRM & Workflow Administrator" при
    # title="CRM / Workflow Administrator".
    candidate_significant = _significant_words(candidate_norm)
    title_significant = _significant_words(title_norm)
    if not candidate_significant or not title_significant:
        return False

    overlap = candidate_significant & title_significant
    smaller = min(len(candidate_significant), len(title_significant))
    return bool(smaller) and len(overlap) / smaller >= TITLE_OVERLAP_THRESHOLD


def _significant_words(text: str) -> set[str]:
    words = {_NON_WORD_RE.sub("", word) for word in text.split()}
    return {word for word in words if len(word) >= 3}


def is_invalid_dou_company(text: str, title: str = "") -> bool:
    """Легка валідація company для DOU title-парсингу.

    Без обмеження кількості слів — офіційні назви військових частин можуть бути
    довгими ("414 окрема бригада безпілотних систем «Птахи Мадяра»").
    """
    if not text:
        return True

    candidate = normalize_apostrophes(text.strip())
    if candidate.endswith((":", ",", ".")):
        return True
    if len(candidate) > MAX_DOU_COMPANY_LENGTH:
        return True
    if not title:
        return False
    if candidate.lower() == title.lower():
        return True
    # Перші 2 слова company збігаються з першими 2 словами title — дублікат
    candidate_words = candidate.lower().split()[:2]
    return bool(candidate_words) and candidate_words == title.lower().split()[:2]


# ── Витягування ───────────────────────────────────────────────────────────────

def extract_company_from_description(description: str) -> str:
    """Витягує назву компанії з HTML-опису вакансії.

    Стратегії в порядку пріоритету:
      1. "<Назва> розробляє/шукає/займається…" — найнадійніший патерн.
      2. "<Назва> — опис" / "<Назва> is a/an/the …" на початку одного з перших абзаців.
    """
    if not description:
        return ""

    text = unescape_twice(description)
    paragraphs = _PARAGRAPH_RE.findall(text) or [text]

    company = _company_before_action_verb(paragraphs)
    if company:
        return company
    return _company_from_intro(paragraphs)


def _company_before_action_verb(paragraphs: list[str]) -> str:
    """Стратегія 1. Обробляємо КОЖЕН <p> окремо, щоб не захопити сусідній абзац."""
    for paragraph in paragraphs:
        plain = html.unescape(strip_tags(paragraph)).strip()
        for match in _VERB_RE.finditer(plain):
            candidate = match.group(1).strip().rstrip(",")
            # Беремо лише останнє речення кандидата — перед дієсловом міг
            # опинитись хвіст попереднього.
            candidate = re.split(r"[\n\.\!:]", candidate)[-1].strip()
            # "група компаній «Промавтоматика» вже 25 років" → "Промавтоматика"
            candidate = _quoted_name(candidate) or candidate
            lowered = candidate.lower()
            if all(word in _SUBJECT_STOP_WORDS for word in lowered.split()):
                continue
            if (candidate
                    and len(candidate) < MAX_COMPANY_LENGTH
                    and lowered not in _SUBJECT_STOP_WORDS
                    and not is_invalid_company(candidate)):
                return candidate
    return ""


def _company_from_intro(paragraphs: list[str]) -> str:
    """Стратегія 2 — позитивний патерн на початку вступних абзаців."""
    for paragraph in paragraphs[:_INTRO_PARAGRAPH_LIMIT]:
        match = _INTRO_RE.match(paragraph)
        if not match:
            continue
        candidate = _TAG_ONLY_RE.sub("", match.group(1)).strip()
        if candidate and not is_invalid_company(candidate):
            return candidate
        after_dash = _company_after_greeting(paragraph, match.end(), candidate)
        if after_dash:
            return after_dash
    return ""


def _company_after_greeting(paragraph: str, dash_end: int, before_dash: str) -> str:
    """«Привіт! Ми — Moodro» — назва стоїть ПРАВОРУЧ від тире, а не ліворуч.

    Спрацьовує лише коли ліворуч самі привітання та займенники: інакше праворуч
    від тире стоїть опис («HostZealot — хостинг…»), і брати його звідти не можна.
    """
    if not _is_greeting(before_dash):
        return ""
    tail = _TAG_ONLY_RE.sub("", paragraph[dash_end:])
    candidate = re.split(r"[\n\.\!\?,:;]", tail)[0].strip()
    candidate = _quoted_name(candidate) or candidate
    # З великої літери — єдине, що відрізняє назву від звичайного іменника:
    # "Ми — Moodro" проти "Вони — лідери ринку". Ціна помилки несиметрична,
    # тож бренд із малої літери краще втратити, ніж підставити опис.
    if not candidate[:1].isupper():
        return ""
    return "" if is_invalid_company(candidate) else candidate


def _is_greeting(text: str) -> bool:
    """Чи складається текст лише з привітань і займенників ("Привіт! Ми")."""
    words = [word.lower().strip(_TRIM_CHARS) for word in text.split()]
    words = [word for word in words if word]
    return bool(words) and all(
        word in _GREETINGS or word in _SUBJECT_STOP_WORDS for word in words
    )


def _quoted_name(text: str) -> str:
    """Витягує назву з лапок — «Промавтоматика» → Промавтоматика."""
    match = _QUOTED_RE.search(text)
    return match.group(1).strip() if match else ""


def trim_company_tagline(company: str) -> str:
    """Обрізає " — опис" (напр. "HostZealot — хостинг, якому довіряють")."""
    if " — " in company:
        return company.split(" — ")[0].strip()
    if " - " in company and len(company.split(" - ")[0]) < 40:
        return company.split(" - ")[0].strip()
    return company


def extract_company(entry, title: str = "", *, parse_description: bool = False) -> str:
    """Витягує компанію: спочатку стандартні RSS-поля, потім — опис.

    Парсинг `<description>` вмикається лише для Djinni: у DOU вся інформація вже
    в `<title>`, і парсинг опису там дає хибні спрацювання.
    """
    for field_name in ("author", "dc_creator", "itunes_author"):
        value = getattr(entry, field_name, None)
        if value and value.strip() and not is_invalid_company(value.strip(), title):
            return value.strip()

    if not parse_description:
        return ""

    description = getattr(entry, "summary", None) or getattr(entry, "description", "")
    candidate = extract_company_from_description(description)
    if is_invalid_company(candidate, title):
        return ""
    return trim_company_tagline(candidate)


__all__ = [
    "extract_company",
    "extract_company_from_description",
    "is_invalid_company",
    "is_invalid_dou_company",
    "trim_company_tagline",
]
