"""Форматування повідомлень з вакансіями (HTML parse mode)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from findmyjob.models import MergedSource, Vacancy

from .texts import period_phrase, vacancies_word

SALARY_UNSPECIFIED = "не вказано"

# Джерела RSS-категорій завжди мають цей суфікс у MergedSource.name
# (findmyjob/feeds/service.py) — у зведенні він винесений у заголовок один раз,
# тож зі списку категорій його прибираємо, щоб не повторювати на кожному рядку.
_RESERVATION_SUFFIX = " (з бронюванням)"

_WHITESPACE_RE = re.compile(r"[\s\xa0]+")
_RANGE_RE = re.compile(r"\d[\s]*[–\-][\s]*\d")
_LEADING_FROM_RE = re.compile(r"\bвід\b\s*", re.IGNORECASE)


def clean_salary(raw: str) -> str:
    """Нормалізує рядок зарплати.

    "від" прибирається лише коли це не діапазон: "від 2000–3000" читається як
    діапазон і без нього, а от "від 2000" без прийменника втратило б сенс.
    """
    if not raw:
        return SALARY_UNSPECIFIED
    text = _WHITESPACE_RE.sub(" ", html.unescape(raw)).strip()
    if not _RANGE_RE.search(text):
        text = _LEADING_FROM_RE.sub("", text).strip()
    return text or SALARY_UNSPECIFIED


def format_date_ua(published: str) -> str:
    """Перетворює дату будь-якого відомого формату на '23.06.2026'.

    Якщо формат нерозпізнаний — повертає рядок як є.
    """
    parsed = None
    try:
        parsed = datetime.strptime(published, "%d.%m.%Y")
    except (ValueError, TypeError):
        pass
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(published)
        except Exception:
            return published
    return f"{parsed.day:02d}.{parsed.month:02d}.{parsed.year}"


def format_vacancy(vacancy: Vacancy) -> str:
    """Підпис під карткою вакансії."""
    lines = [f"💼 <b>Спеціальність:</b> {html.escape(vacancy.title)}"]
    if vacancy.company:
        lines.append(f"🏢 <b>Компанія:</b> {html.escape(vacancy.company)}")
    if vacancy.location:
        lines.append(f"📍 <b>Місце роботи:</b> {html.escape(vacancy.location)}")

    salary = clean_salary(vacancy.salary)
    if salary and salary != SALARY_UNSPECIFIED:
        lines.append(f"💰 <b>Зарплата:</b> {html.escape(salary)}")

    if vacancy.published:
        lines.append(
            f"📅 <b>Дата публікації:</b> {html.escape(format_date_ua(vacancy.published))}"
        )
    lines.append(f"🌐 {_source_line(vacancy.source)}")
    return "\n".join(lines)


def _source_line(source: str) -> str:
    """"DOU (Deftech) Python" → "<b>DOU:</b> (Deftech) Python" — назва сайту
    (DOU / Djinni / NDA, перше слово source) жирним і з двокрапкою замість
    спільного для всіх джерел підпису "Фільтр:"."""
    site, _, rest = source.partition(" ")
    site_html = f"<b>{html.escape(site)}:</b>"
    return f"{site_html} {html.escape(rest)}" if rest else site_html


def build_summary(sources: list[MergedSource], days: int | None, hidden_count: int = 0) -> str:
    """Зведення по джерелах.

    `sources` — вакансії ПІСЛЯ вилучення прихованих (тобто доступні до перегляду).
    `hidden_count` — скільки з поточної вибірки збігається зі списком вилучених.
    """
    available = sum(len(source.vacancies) for source in sources)
    total_found = available + hidden_count

    header = (
        f"{period_phrase(days).capitalize()} знайдено - "
        f"{total_found} {vacancies_word(total_found)} з бронюванням"
    )
    if hidden_count:
        header = (
            f"{header}, з них - {hidden_count} в списку вилучених.\n"
            f"До перегляду доступно {available} {vacancies_word(available)}:"
        )
    else:
        header += ":"

    lines = [f"🗂 <b>{header}</b>\n"]
    for source in sources:
        count = len(source.vacancies)
        name = source.name.removesuffix(_RESERVATION_SUFFIX)
        lines.append(f"  • {name} ({count})")
    return "\n".join(lines)
