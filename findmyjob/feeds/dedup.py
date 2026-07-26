"""Злиття списків вакансій із дедуплікацією.

Дві різні стратегії, бо «однакова вакансія» означає різне на різних рівнях:

* `merge_by_link` — у межах ОДНОГО сайту (Deftech-варіант + звичайний): та сама
  вакансія має буквально той самий URL.
* `merge_by_title_and_company` — між РІЗНИМИ сайтами (DOU + Djinni): URL там
  завжди різні, тож порівнювати їх марно, і однаковість визначає пара
  (назва, компанія).
"""

from __future__ import annotations

from dataclasses import dataclass

from findmyjob.models import Vacancy

from .diagnostics import log_dedup, log_duplicate_pair


@dataclass(frozen=True)
class MergeResult:
    vacancies: list[Vacancy]
    duplicates: int


def merge_by_link(name: str, primary: list[Vacancy], secondary: list[Vacancy]) -> MergeResult:
    """Об'єднує два списки одного сайту, прибираючи з `secondary` збіги за URL."""
    _log_start(name, primary, secondary)

    seen_links = {v.link for v in primary if v.link}
    unique: list[Vacancy] = []
    duplicates = 0

    for vacancy in secondary:
        if vacancy.link and vacancy.link in seen_links:
            # Пари дублікатів тут навмисно не логуємо — детально їх друкуємо
            # тільки для фінального злиття (merge_by_title_and_company).
            duplicates += 1
            continue
        if vacancy.link:
            seen_links.add(vacancy.link)
        unique.append(vacancy)

    return _finish(name, primary + unique, duplicates)


def merge_by_title_and_company(
    name: str, primary: list[Vacancy], secondary: list[Vacancy]
) -> MergeResult:
    """Об'єднує списки різних сайтів за парою (назва, компанія).

    Якщо компанія порожня — порівнюємо тільки за назвою.
    """
    _log_start(name, primary, secondary)
    index = _TitleCompanyIndex()
    for vacancy in primary:
        index.add(vacancy)

    unique: list[Vacancy] = []
    duplicates = 0

    for vacancy in secondary:
        original = index.add(vacancy)
        if original is None:
            unique.append(vacancy)
        else:
            log_duplicate_pair(f"У {name}", original, vacancy)
            duplicates += 1

    return _finish(name, primary + unique, duplicates)


class _TitleCompanyIndex:
    """Індекс уже побачених вакансій за назвою та парою (назва, компанія).

    Зберігає саму вакансію, а не лише ключ, щоб при виявленні дубліката можна
    було залогувати ОБИДВА записи разом з посиланнями.
    """

    def __init__(self) -> None:
        self._by_title_and_company: dict[tuple[str, str], Vacancy] = {}
        self._titles_with_company: dict[str, Vacancy] = {}
        self._titles_without_company: dict[str, Vacancy] = {}

    def add(self, vacancy: Vacancy) -> Vacancy | None:
        """Реєструє вакансію. Повертає раніше збережений дублікат або None."""
        title = vacancy.title.strip().lower()
        company = vacancy.company.strip().lower()

        if not title:
            # Порожня назва — порівнювати нема з чим, завжди залишаємо
            return None

        if company:
            existing = self._by_title_and_company.get((title, company))
            if existing is not None:
                return existing
            self._by_title_and_company[(title, company)] = vacancy
            self._titles_with_company[title] = vacancy
            return None

        existing = self._titles_without_company.get(title) or self._titles_with_company.get(title)
        if existing is not None:
            return existing
        self._titles_without_company[title] = vacancy
        return None


def _log_start(name: str, primary: list[Vacancy], secondary: list[Vacancy]) -> None:
    log_dedup("[MERGE → %s] primary=%d, secondary=%d", name, len(primary), len(secondary))


def _finish(name: str, combined: list[Vacancy], duplicates: int) -> MergeResult:
    log_dedup("  ✅ %d вак. (видалено %d)", len(combined), duplicates)
    return MergeResult(vacancies=combined, duplicates=duplicates)
