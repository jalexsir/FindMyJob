"""Розбір DOU-формату заголовка: "Посада в/на/у Компанія, [Зарплата,] Місто".

DOU пакує всю інформацію про вакансію в один рядок `<title>`, тому і компанію, і
зарплату, і місто доводиться відновлювати з нього. Проблема в тому, що після
роздільника може стояти не назва компанії, а початок опису — звідси кілька
проходів і перевірок валідності.
"""

from __future__ import annotations

from dataclasses import dataclass

from .company import is_invalid_dou_company, trim_company_tagline
from .text import SALARY_RE

# Роздільники між посадою та рештою рядка. Порядок не важливий — беремо той,
# що трапився раніше в рядку.
SEPARATORS = (" в ", " на ", " у ")

# Якщо весь залишок після роздільника містить ці маркери — це опис, а не назва
# компанії, і треба одразу шукати наступний роздільник.
DESCRIPTION_MARKERS = (
    "що потрібний", "ми шукаємо", "займаємося", "цінності",
    "обов'язки", "вимоги", "умови", "пропонуємо",
)

MAX_CITY_LENGTH = 25


@dataclass(frozen=True)
class ParsedTitle:
    """Результат розбору DOU-заголовка."""

    title: str
    company: str = ""
    salary: str = ""
    location: str = ""


def parse_dou_title(raw_title: str) -> ParsedTitle:
    """Розбирає DOU-заголовок. Якщо роздільника немає — усе вважається посадою."""
    separator_index, separator = _find_first_separator(raw_title)
    if separator_index == -1:
        return ParsedTitle(title=raw_title)

    title = raw_title[:separator_index].strip()
    remainder = raw_title[separator_index + len(separator):]
    parts = [part.strip() for part in remainder.split(",")]
    if not parts:
        return ParsedTitle(title=title)

    # Залишок — очевидний опис: назву компанії шукаємо за наступним роздільником.
    if _has_description_marker(remainder):
        rescued = _rescue_company(remainder, title)
        return rescued if rescued else ParsedTitle(title=title)

    company, salary, location = _split_leading_parts(parts)

    # Зарплата могла злипнутися з назвою компанії
    if not salary:
        company, salary = _extract_embedded_salary(company)

    # "Назва, Місто" — і location ще порожній
    if not location and "," in company:
        company, location = split_company_and_cities(company)

    if is_invalid_dou_company(company, title):
        rescued = _rescue_company(remainder, title, salary=salary, location=location)
        if rescued is None:
            company = ""
        else:
            company, salary, location = rescued.company, rescued.salary, rescued.location

    return ParsedTitle(
        title=title,
        company=trim_company_tagline(company),
        salary=salary,
        location=location,
    )


def split_company_and_cities(company: str) -> tuple[str, str]:
    """Розділяє "Назва, Місто1, Місто2" → ("Назва", "Місто1, Місто2").

    Логіка: якщо останні частини після коми — короткі слова (< 25 символів) без
    цифр і без ознак зарплати — це міста.
    """
    if "," not in company:
        return company, ""

    company_parts = [part.strip() for part in company.split(",")]
    city_parts: list[str] = []

    # Ідемо з кінця: збираємо міста, поки вони схожі на міста
    while len(company_parts) > 1:
        candidate = company_parts[-1].strip()
        if not _looks_like_city(candidate):
            break
        city_parts.insert(0, candidate)
        company_parts.pop()

    return ", ".join(company_parts).strip(), ", ".join(city_parts).strip()


# ── Внутрішні кроки розбору ───────────────────────────────────────────────────

def _find_first_separator(raw_title: str) -> tuple[int, str | None]:
    best_index, best_separator = -1, None
    for separator in SEPARATORS:
        index = raw_title.find(separator)
        if index != -1 and (best_index == -1 or index < best_index):
            best_index, best_separator = index, separator
    return best_index, best_separator


def _has_description_marker(remainder: str) -> bool:
    lowered = remainder.lower()
    return any(marker in lowered for marker in DESCRIPTION_MARKERS)


def _looks_like_city(candidate: str) -> bool:
    return bool(
        candidate
        and len(candidate) < MAX_CITY_LENGTH
        and not SALARY_RE.search(candidate)
        and not candidate.endswith(":")
        and not any(char.isdigit() for char in candidate)
    )


def _split_leading_parts(parts: list[str]) -> tuple[str, str, str]:
    """Розкладає частини після роздільника на (company, salary, location)."""
    company, salary, location = parts[0], "", ""

    if len(parts) == 2:
        candidate = parts[1].strip()
        if SALARY_RE.search(candidate):
            salary = candidate
        else:
            location = candidate
    elif len(parts) >= 3:
        if SALARY_RE.search(parts[1]):
            salary = parts[1].strip()
            location = ", ".join(parts[2:]).strip()
        else:
            # Зарплата може стояти й далі в рядку — шукаємо її серед решти
            rest = parts[1:]
            salary_index = next(
                (i for i, part in enumerate(rest) if SALARY_RE.search(part)), None
            )
            if salary_index is None:
                location = ", ".join(rest).strip()
            else:
                salary = rest[salary_index].strip()
                location = ", ".join(rest[salary_index + 1:]).strip()

    return company, salary, location


def _extract_embedded_salary(company: str) -> tuple[str, str]:
    """Витягує зарплату, що потрапила всередину назви компанії."""
    match = SALARY_RE.search(company)
    if not match:
        return company, ""
    salary = match.group(0).strip()
    cleaned = company[:match.start()].strip().rstrip(",").strip()
    return cleaned, salary


def _rescue_company(
    remainder: str, title: str, *, salary: str = "", location: str = ""
) -> ParsedTitle | None:
    """Шукає назву компанії за НАСТУПНИМ роздільником усередині залишку.

    Потрібно, коли одразу після першого роздільника йде не компанія, а опис
    ("Розробник в компанію, що займається ... в Acme, Київ").

    `salary`/`location` — значення, вже здобуті з основного розбору: якщо в
    хвості після знайденої компанії їх немає, вони лишаються без змін.
    Повертає None, якщо валідного кандидата немає.
    """
    for separator in SEPARATORS:
        index = remainder.find(separator)
        if index == -1:
            continue
        parts = [part.strip() for part in remainder[index + len(separator):].split(",")]
        if not parts or is_invalid_dou_company(parts[0], title):
            continue
        new_salary, new_location = _salary_and_location(parts, salary, location)
        return ParsedTitle(
            title=title, company=parts[0], salary=new_salary, location=new_location
        )
    return None


def _salary_and_location(parts: list[str], salary: str, location: str) -> tuple[str, str]:
    """Розкладає хвіст після назви компанії на (salary, location).

    Якщо хвоста немає (parts — лише сама компанія), повертає значення без змін.
    """
    if len(parts) == 2:
        candidate = parts[1].strip()
        return (candidate, location) if SALARY_RE.search(candidate) else (salary, candidate)
    if len(parts) >= 3:
        if SALARY_RE.search(parts[1]):
            return parts[1].strip(), ", ".join(parts[2:]).strip()
        return salary, parts[-1].strip()
    return salary, location
