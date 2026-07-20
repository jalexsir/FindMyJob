"""
Генерація картинки-заголовка для вакансії.
Синій градієнтний фон + назва вакансії білим жирним текстом.
"""

import io
import textwrap
from PIL import Image, ImageDraw, ImageFont

# Розмір картинки
IMG_W, IMG_H = 800, 300

# Кольори градієнту (зліва направо: темно-синій → яскраво-синій)
COLOR_LEFT  = (15, 32, 90)    # #0F205A
COLOR_RIGHT = (26, 117, 255)  # #1A75FF

# Колір тексту
COLOR_TEXT       = (255, 255, 255)   # білий
COLOR_TEXT_SHADOW = (0, 0, 0, 120)  # напівпрозора тінь

# Максимальна ширина рядка (символів) для переносу
WRAP_WIDTH = 28


def _make_gradient(width: int, height: int) -> Image.Image:
    """Малює горизонтальний лінійний градієнт."""
    img = Image.new("RGB", (width, height))
    for x in range(width):
        t = x / (width - 1)
        r = int(COLOR_LEFT[0] + (COLOR_RIGHT[0] - COLOR_LEFT[0]) * t)
        g = int(COLOR_LEFT[1] + (COLOR_RIGHT[1] - COLOR_LEFT[1]) * t)
        b = int(COLOR_LEFT[2] + (COLOR_RIGHT[2] - COLOR_LEFT[2]) * t)
        for y in range(height):
            img.putpixel((x, y), (r, g, b))
    return img


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Намагається завантажити шрифт; якщо не знайдено — використовує дефолтний."""
    candidates = [
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def generate_vacancy_image(title: str, num: int = 0) -> io.BytesIO:
    """
    Генерує PNG-картинку з назвою вакансії на синьому градієнтному фоні.
    Повертає BytesIO готовий для відправки в Telegram.
    """
    img = _make_gradient(IMG_W, IMG_H)
    draw = ImageDraw.Draw(img, "RGBA")

    # Невеликий напівпрозорий темний прямокутник в центрі для читабельності
    padding = 40
    draw.rectangle(
        [padding, padding, IMG_W - padding, IMG_H - padding],
        fill=(0, 0, 0, 60),
    )

    # Номер вакансії — маленький текст угорі
    if num:
        font_num = _get_font(22)
        draw.text((60, 55), f"Вакансія #{num}", font=font_num, fill=(200, 220, 255))

    # Назва вакансії — великий жирний текст по центру
    font_title = _get_font(52)
    wrapped = textwrap.fill(title, width=WRAP_WIDTH)

    # Розраховуємо bbox для центрування
    bbox = draw.textbbox((0, 0), wrapped, font=font_title)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (IMG_W - text_w) // 2
    y = (IMG_H - text_h) // 2 + (15 if num else 0)

    # Тінь
    draw.text((x + 2, y + 2), wrapped, font=font_title, fill=(0, 0, 0, 140), align="center")
    # Основний текст
    draw.text((x, y), wrapped, font=font_title, fill=COLOR_TEXT, align="center")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf