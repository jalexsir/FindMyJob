"""Генерація картки вакансії (бренд-стиль FIND MY JOB: чорний фон, жовті
пігулки-плашки, білий жирний текст, синьо-жовтий прапор-акцент)."""

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
# Однаковий відступ фону пігулки від країв тексту з усіх боків — спільний
# для всіх трьох плашок (номер, NEW, обране), щоб виглядали одним стилем.
PILL_PADDING_X = 12
PILL_PADDING_Y = 6
# Прапор-акцент зліва від номера вакансії — той самий мотив, що й у логотипі
# FIND MY JOB (вертикальна синьо-жовта смуга перед текстом).
FLAG_BAR_WIDTH = 8
FLAG_BAR_GAP = 8

# ── Палітра (виміряно з референсного логотипу) ────────────────────────────────
BACKGROUND = (0, 0, 0)
YELLOW = (249, 219, 80)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
FLAG_BLUE = (65, 125, 210)
BADGE_NEW_FILL = (200, 30, 30)
BADGE_NEW_OUTLINE = (255, 80, 80)

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
        """Картинка фіксованого розміру: чорний блок із жовтою рамкою на весь канвас."""
        wrapped = textwrap.fill(title, width=WRAP_WIDTH)
        title_width, title_height = self._measure_title(wrapped)

        width = CANVAS_WIDTH
        height = max(CANVAS_HEIGHT, self._content_height(title_height, num, is_new, is_favorite))

        image = Image.new("RGB", (width, height), BACKGROUND)
        draw = ImageDraw.Draw(image, "RGBA")
        right, bottom = width - 1, height - 1

        draw.rounded_rectangle(
            [0, 0, right, bottom], radius=FRAME_CORNER_RADIUS, outline=YELLOW, width=3
        )

        if num:
            self._draw_number_badge(draw, num)
        if is_new:
            self._draw_new_badge(draw, right)

        # Назва — по центру всього канвасу, плоским білим жирним текстом
        # (без тіні — так само, як у референсному логотипі).
        draw.text(
            ((width - title_width) // 2, (height - title_height) // 2),
            wrapped, font=self._font_title, fill=WHITE, align="center",
        )

        if is_favorite:
            self._draw_favorite_badge(draw, bottom)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    # ── Внутрішні деталі ─────────────────────────────────────────────────────

    def _measure_title(self, wrapped: str) -> tuple[int, int]:
        left, top, right, bottom = self._measuring_draw().textbbox(
            (0, 0), wrapped, font=self._font_title
        )
        return right - left, bottom - top

    def _content_height(self, title_height: int, num: int, is_new: bool, is_favorite: bool) -> int:
        """Мінімальна висота, за якої вміст точно поміститься в рамку."""
        draw = self._measuring_draw()
        number_height = self._pill_size(draw, "Вакансія #99", self._font_number)[2] if num else 0
        new_height = self._pill_size(draw, BADGE_NEW, self._font_new)[2] if is_new else 0
        favorite_height = (
            self._pill_size(draw, BADGE_FAVORITE, self._font_favorite)[2] if is_favorite else 0
        )

        top_row = max(number_height, new_height) if (num or is_new) else 0
        content = (
            (top_row + INNER_PADDING if top_row else 0)
            + title_height
            + (INNER_PADDING + favorite_height if favorite_height else 0)
        )
        return content + INNER_PADDING * 2

    @staticmethod
    def _measuring_draw() -> ImageDraw.ImageDraw:
        """Draw-контекст на 1×1 піксель — потрібен лише для textbbox-вимірів
        перед тим, як відомий фінальний розмір картинки, не для малювання."""
        return ImageDraw.Draw(Image.new("RGB", (1, 1)))

    @staticmethod
    def _pill_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[tuple, int, int]:
        """Єдине місце, де пігулка перетворюється з тексту на розмір:
        (bbox, ширина, висота) з відступом PILL_PADDING_X/Y звідусіль."""
        box = draw.textbbox((0, 0), text, font=font)
        width = (box[2] - box[0]) + PILL_PADDING_X * 2
        height = (box[3] - box[1]) + PILL_PADDING_Y * 2
        return box, width, height

    def _draw_number_badge(self, draw: ImageDraw.ImageDraw, num: int) -> None:
        """Жовта пігулка "Вакансія #N" у лівому верхньому куті, з прапором-
        акцентом зліва (той самий мотив, що й у логотипі FIND MY JOB)."""
        text = f"Вакансія #{num}"
        _, height = self._draw_pill(
            draw, text, self._font_number,
            x=INNER_PADDING + FLAG_BAR_WIDTH + FLAG_BAR_GAP, y=INNER_PADDING,
            fill=YELLOW, text_color=BLACK, align="left",
        )
        self._draw_flag_bar(draw, INNER_PADDING, INNER_PADDING, height)

    def _draw_new_badge(self, draw: ImageDraw.ImageDraw, right: int) -> None:
        """Червона пігулка NEW у правому верхньому куті — єдиний акцент не
        жовтого кольору, щоб миттєво відрізнялась від "фонових" плашок."""
        self._draw_pill(
            draw, BADGE_NEW, self._font_new,
            x=right - INNER_PADDING, y=INNER_PADDING,
            fill=BADGE_NEW_FILL, text_color=WHITE,
            outline=BADGE_NEW_OUTLINE, outline_width=2, align="right",
        )

    def _draw_favorite_badge(self, draw: ImageDraw.ImageDraw, bottom: int) -> None:
        """Жовта пігулка "* Обране *" у лівому нижньому куті — прив'язана до
        нижнього краю через valign="bottom", тож висоту наперед рахувати не
        треба (цим уже займається сам _draw_pill)."""
        self._draw_pill(
            draw, BADGE_FAVORITE, self._font_favorite,
            x=INNER_PADDING, y=bottom - INNER_PADDING,
            fill=YELLOW, text_color=BLACK, align="left", valign="bottom",
        )

    @staticmethod
    def _draw_flag_bar(draw: ImageDraw.ImageDraw, x: int, y: int, height: int) -> None:
        """Вертикальна синьо-жовта смуга — компактний прапор-акцент."""
        half = height // 2
        draw.rectangle([x, y, x + FLAG_BAR_WIDTH, y + half], fill=FLAG_BLUE)
        draw.rectangle([x, y + half, x + FLAG_BAR_WIDTH, y + height], fill=YELLOW)

    def _draw_pill(
        self, draw: ImageDraw.ImageDraw, text: str, font, *, x: int, y: int,
        fill, text_color, align: str = "left", valign: str = "top",
        outline=None, outline_width: int = 0,
    ) -> tuple[int, int]:
        """Малює заокруглену пігулку з текстом усередині.

        `x`/`align` — ліва (align="left") чи права (align="right") межа
        плашки. `y`/`valign` — те саме по вертикалі: верхня (valign="top")
        чи нижня (valign="bottom") межа. Розмір бере з `_pill_size` — та сама
        логіка, що й для попереднього підрахунку висоти картинки, тож
        відступ від країв тексту завжди однаковий з усіх боків: `textbbox`
        рахує ink-межі відносно точки малювання (не (0, 0) — трохи зсунуті
        через метрику шрифту), тож точку малювання тексту компенсуємо на
        -box[0]/-box[1], щоб ink-межі влучили точно в центр плашки. Повертає
        (ширину, висоту) плашки.
        """
        box, width, height = self._pill_size(draw, text, font)
        x1 = x if align == "left" else x - width
        y1 = y if valign == "top" else y - height
        x2, y2 = x1 + width, y1 + height
        radius = height // 2

        draw.rounded_rectangle(
            [x1, y1, x2, y2], radius=radius, fill=fill,
            outline=outline, width=outline_width if outline else 0,
        )
        draw.text(
            (x1 + PILL_PADDING_X - box[0], y1 + PILL_PADDING_Y - box[1]),
            text, font=font, fill=text_color,
        )
        return width, height
