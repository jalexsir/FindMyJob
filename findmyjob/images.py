"""Генерація картки вакансії (Minecraft-стиль: темний блок із золотою рамкою)."""

from __future__ import annotations

import io
import textwrap
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

CANVAS_WIDTH, CANVAS_HEIGHT = 800, 300
WRAP_WIDTH = 26
INNER_PADDING = 12
# Невелике заокруглення кутів рамки — суто косметика, тому не пов'язане з
# INNER_PADDING (той рахує місце під текст, а не форму самої рамки).
FRAME_CORNER_RADIUS = 16
# Відступ червоного фону плашки NEW від країв літер — однаковий з усіх боків.
NEW_BADGE_PADDING = 5

# ── Палітра ───────────────────────────────────────────────────────────────────
BACKGROUND = (15, 15, 45)
GOLD = (255, 215, 0)
WHITE = (255, 255, 255)
TEXT_SHADOW = (80, 40, 0)
BADGE_FILL = (200, 30, 30)
BADGE_OUTLINE = (255, 80, 80)

# ── Розміри шрифтів ───────────────────────────────────────────────────────────
FONT_SIZE_NUMBER = 26
FONT_SIZE_NEW = 22
FONT_SIZE_FAVORITE = 20
FONT_SIZE_TITLE = 42

BADGE_NEW = "NEW"
BADGE_FAVORITE = "* Обране *"

# Кандидати шрифтів для різних ОС — беремо перший наявний
FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


@lru_cache(maxsize=None)
def load_font(size: int):
    """Шрифт заданого розміру. Кешується: рендер сотні карток інакше означав би
    сотні відкриттів одних і тих самих TTF-файлів."""
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


class VacancyImageRenderer:
    """Малює картку вакансії й повертає готовий PNG у пам'яті."""

    def __init__(self) -> None:
        self._font_number = load_font(FONT_SIZE_NUMBER)
        self._font_new = load_font(FONT_SIZE_NEW)
        self._font_favorite = load_font(FONT_SIZE_FAVORITE)
        self._font_title = load_font(FONT_SIZE_TITLE)

    def render(
        self,
        title: str,
        num: int = 0,
        *,
        is_new: bool = False,
        is_favorite: bool = False,
    ) -> io.BytesIO:
        """Картинка фіксованого розміру: темний блок із золотою рамкою на весь канвас."""
        wrapped = textwrap.fill(title, width=WRAP_WIDTH)
        title_width, title_height = self._measure_title(wrapped)

        width = CANVAS_WIDTH
        height = max(CANVAS_HEIGHT, self._content_height(title_height, num, is_new, is_favorite))

        image = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(image, "RGBA")
        right, bottom = width - 1, height - 1

        draw.rounded_rectangle(
            [0, 0, right, bottom], radius=FRAME_CORNER_RADIUS, outline=GOLD, width=3
        )

        if num:
            self._draw_shadowed_text(
                draw, f"Вакансія #{num}",
                INNER_PADDING, INNER_PADDING, self._font_number, GOLD,
            )
        if is_new:
            self._draw_new_badge(draw, right)

        # Назва — по центру всього канвасу
        self._draw_shadowed_text(
            draw, wrapped,
            (width - title_width) // 2, (height - title_height) // 2,
            self._font_title, WHITE,
        )

        if is_favorite:
            box = draw.textbbox((0, 0), BADGE_FAVORITE, font=self._font_favorite)
            badge_height = box[3] - box[1]
            self._draw_shadowed_text(
                draw, BADGE_FAVORITE,
                INNER_PADDING, bottom - badge_height - INNER_PADDING,
                self._font_favorite, GOLD,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    # ── Внутрішні деталі ─────────────────────────────────────────────────────

    def _measure_title(self, wrapped: str) -> tuple[int, int]:
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        left, top, right, bottom = draw.textbbox((0, 0), wrapped, font=self._font_title)
        return right - left, bottom - top

    def _content_height(self, title_height: int, num: int, is_new: bool, is_favorite: bool) -> int:
        """Мінімальна висота, за якої вміст точно поміститься в рамку."""
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        number_height = draw.textbbox((0, 0), "Вакансія #99", font=self._font_number)[3] if num else 0
        new_height = draw.textbbox((0, 0), BADGE_NEW, font=self._font_new)[3] if is_new else 0
        favorite_height = (
            draw.textbbox((0, 0), BADGE_FAVORITE, font=self._font_favorite)[3] if is_favorite else 0
        )

        top_row = max(number_height, new_height) if (num or is_new) else 0
        content = (
            (top_row + INNER_PADDING if top_row else 0)
            + title_height
            + (INNER_PADDING + favorite_height if favorite_height else 0)
        )
        return content + INNER_PADDING * 2

    def _draw_new_badge(self, draw: ImageDraw.ImageDraw, right: int) -> None:
        """Червона плашка NEW у правому верхньому куті.

        Відступ фону від країв літер має бути однаковим з усіх боків, а
        `textbbox` рахує ink-межі відносно точки малювання (яка не
        збігається з (0, 0) через метрику шрифту — box[0]/box[1] зазвичай
        трохи ненульові). Тому спершу міряємо bbox у (0, 0), будуємо
        прямокутник бажаного розміру навколо нього, а тоді зсуваємо саму
        точку малювання тексту на -box[0]/-box[1], щоб ink-межі влучили
        точно всередину прямокутника з відступом NEW_BADGE_PADDING звідусіль.
        """
        box = draw.textbbox((0, 0), BADGE_NEW, font=self._font_new)
        text_width, text_height = box[2] - box[0], box[3] - box[1]

        rect_x2 = right - INNER_PADDING
        rect_y1 = INNER_PADDING
        rect_x1 = rect_x2 - text_width - NEW_BADGE_PADDING * 2
        rect_y2 = rect_y1 + text_height + NEW_BADGE_PADDING * 2

        draw.rectangle(
            [rect_x1, rect_y1, rect_x2, rect_y2], fill=BADGE_FILL, outline=BADGE_OUTLINE, width=2,
        )
        text_x = rect_x1 + NEW_BADGE_PADDING - box[0]
        text_y = rect_y1 + NEW_BADGE_PADDING - box[1]
        draw.text((text_x, text_y), BADGE_NEW, font=self._font_new, fill=WHITE)

    @staticmethod
    def _draw_shadowed_text(draw, text: str, x: int, y: int, font, color) -> None:
        """Текст із товстою (3 пікселі) тінню — характерний Minecraft-вигляд."""
        for offset_x, offset_y in ((3, 3), (3, 0), (0, 3)):
            draw.text((x + offset_x, y + offset_y), text, font=font,
                      fill=TEXT_SHADOW, align="center")
        draw.text((x, y), text, font=font, fill=color, align="center")
