"""Категорії вакансій та побудова RSS-джерел для них.

Список категорій і DOU-слаги — з офіційного `<select name="category">` на
jobs.dou.ua/vacancies/. Djinni-ключове слово — вільнотекстовий пошук
(all_keywords); None означає, що для цієї категорії Djinni-джерел немає взагалі
(перевірено емпірично — або нема адекватного відповідника, або ключове слово
дає лише шум).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

# Найпопулярніші мови програмування — навмисно на початку списку (перші сторінки
# вибору категорій у боті), решта — після них.
AVAILABLE_CATEGORIES = [
    "Python", "Java", "Kotlin", "C++", "NET", "Front End", "Node.js", "Golang", "Rust",
    "AI/ML", "Support", "Architect", "C-level", "Copywriter",
    "Data Engineer", "Data Science", "Design", "DevOps", "Embedded",
    "Engineering Manager", "ERP/CRM", "PHP", "Hardware",
    "Marketing", "Product Manager", "Project Manager",
    "QA", "SAP", "Security", "Analyst", "SysAdmin",
    "Technical Writer", "Unity", "Unreal Engine",
]

DOU_CATEGORY_MAP = {
    "NET": ".NET", "AI/ML": "AI/ML", "Analyst": "Analyst", "Architect": "Architect",
    "C++": "C++", "C-level": "C-level", "Copywriter": "Copywriter",
    "Data Engineer": "Data Engineer", "Data Science": "Data Science", "Design": "Design",
    "DevOps": "DevOps", "Embedded": "Embedded", "Engineering Manager": "Engineering Manager",
    "ERP/CRM": "ERP/CRM", "Front End": "Front End", "Golang": "Golang", "Hardware": "Hardware",
    "Java": "Java", "Kotlin": "Kotlin", "Marketing": "Marketing", "Node.js": "Node.js", "PHP": "PHP",
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
    "Java": "java", "Kotlin": "kotlin", "Marketing": "marketing", "Node.js": "node.js", "PHP": "php",
    "Product Manager": "product manager", "Project Manager": "project manager",
    "Python": "python", "QA": "qa", "Rust": "rust", "SAP": "sap", "Security": "security",
    "Support": "support", "SysAdmin": "sysadmin", "Technical Writer": "technical writer",
    "Unity": "unity", "Unreal Engine": "unreal engine",
}


class Site(str, Enum):
    """Сайт-джерело вакансій. Значення входить у видиму назву джерела."""

    DOU = "DOU"
    DJINNI = "Djinni"


class Variant(str, Enum):
    """Різновид пошуку в межах сайту. Значення входить у видиму назву джерела."""

    DEFTECH = "(Deftech)"
    RESERVATION = "(бронювання)"


@dataclass(frozen=True)
class FeedSource:
    """Один RSS-ендпоінт.

    `name` використовується і як ключ внутрішніх словників, і як значення
    `Vacancy.source` — тобто те, що показується у полі "Фільтр" під вакансією.
    """

    site: Site
    variant: Variant
    category: str
    url: str

    @property
    def name(self) -> str:
        return f"{self.site.value} {self.variant.value} {self.category}"


def _dou_url(dou_category: str, variant: Variant) -> str:
    """Фільтр по категорії ПЛЮС пошук двома термами через кому.

    Формат узято з самого DOU — так виглядає стрічка, яку він генерує для
    сторінки пошуку: `?category=Java&search=бронювання, java`. Категорія лишається
    фільтром по таксономії, а другим термом дублюється в самому запиті, як це
    робить сайт. `descr=1` не додаємо: у стрічках DOU його немає.
    """
    variant_term = "miltech" if variant is Variant.DEFTECH else "бронювання"
    search = quote(f"{variant_term}, {dou_category.lower()}")
    return (
        f"https://jobs.dou.ua/vacancies/feeds/"
        f"?category={quote(dou_category)}&search={search}"
    )


def _djinni_url(keyword: str, variant: Variant) -> str:
    """Базовий пошук по полях, а не повнотекстовий.

    `search_type=full-text` пробували — він тягне забагато вакансій, що до
    запиту стосунку не мають: ключове слово трапляється десь у тілі опису.
    """
    editorial = "miltech" if variant is Variant.DEFTECH else "reservation"
    return (
        f"https://djinni.co/jobs/rss/?all_keywords={quote(keyword)}"
        f"&search_type=basic-search&editorial={editorial}"
    )


def build_sources_for_category(category: str) -> list[FeedSource]:
    """Будує 4 (або 2, якщо Djinni-ключа немає) RSS-джерела для однієї категорії."""
    dou_category = DOU_CATEGORY_MAP.get(category, category)
    djinni_keyword = DJINNI_KEYWORD_MAP.get(category, category.lower())

    sources = [
        FeedSource(Site.DOU, variant, category, _dou_url(dou_category, variant))
        for variant in Variant
    ]
    if djinni_keyword:
        sources += [
            FeedSource(
                Site.DJINNI, variant, category, _djinni_url(djinni_keyword, variant)
            )
            for variant in Variant
        ]
    return sources


def build_sources(categories: list[str]) -> list[FeedSource]:
    """Усі RSS-джерела для набору категорій."""
    return [source for category in categories for source in build_sources_for_category(category)]
