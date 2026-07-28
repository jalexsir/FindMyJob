"""Низькорівневі текстові утиліти, спільні для всіх парсерів фідів."""

from __future__ import annotations

import html
import re

# Зарплата: "$3000", "від 50 000 грн", "2000–3000 $", "80k" тощо.
SALARY_RE = re.compile(
    r"""
    (?:від\s+)?(?:\$|€|£|грн\.?)\s*[\d\s]+(?:[–\-]\s*[\d\s]+)?(?:\+)?(?:\s*(?:тис|k|K))?
    |(?:від\s+)?[\d\s]+(?:[–\-]\s*[\d\s]+)?\s*(?:тис\.?\s*)?(?:\$|€|£|грн\.?)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Кириличні літери-двійники латинських — назви компаній часто пишуть змішано.
_HOMOGLYPHS = str.maketrans({
    "С": "C", "с": "c", "Е": "E", "е": "e", "О": "O", "о": "o",
    "Р": "P", "р": "p", "Х": "X", "х": "x", "А": "A", "а": "a",
    "В": "B", "в": "b", "Н": "H", "н": "h", "К": "K", "к": "k",
    "М": "M", "м": "m", "Т": "T", "т": "t",
    "І": "I", "і": "i", "У": "y", "у": "y",
})


def normalize_homoglyphs(value: str) -> str:
    """Замінює кириличні літери-двійники (С→C, Е→E тощо) на латинські."""
    return value.translate(_HOMOGLYPHS)


def unescape_twice(value: str) -> str:
    """RSS-описи часто приходять двічі екранованими (`&amp;lt;p&amp;gt;`)."""
    return html.unescape(html.unescape(value))


def strip_tags(value: str) -> str:
    """Прибирає HTML-теги, лишаючи пробіл на їх місці."""
    return _TAG_RE.sub(" ", value)


def collapse_spaces(value: str) -> str:
    """Стискає будь-які пробільні послідовності до одного пробілу."""
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_apostrophes(value: str) -> str:
    """Типографські апострофи → звичайний."""
    return value.replace("’", "'").replace("‘", "'")
