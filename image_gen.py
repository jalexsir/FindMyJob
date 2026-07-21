"""
Генерація картинки-заголовка для вакансії у стилі Minecraft.
Зелена трава зверху, коричнева земля внизу, піксельна сітка, жовтий/білий текст.
"""

import io
import random
import textwrap
from PIL import Image, ImageDraw, ImageFont

IMG_W, IMG_H = 800, 300
WRAP_WIDTH = 26

# ── Minecraft-палітра ─────────────────────────────────────────────────────────
MC_GRASS_TOP    = (89, 142, 48)     # яскраво-зелений верх трави
MC_GRASS_SIDE   = (106, 127, 61)    # зелений бік блоку
MC_DIRT         = (121, 85, 58)     # коричнева земля
MC_DIRT_DARK    = (96, 66, 44)      # темно-коричнева земля
MC_STONE        = (128, 128, 128)   # сірий камінь
MC_STONE_DARK   = (100, 100, 100)   # темний камінь
MC_SKY_TOP      = (116, 164, 225)   # небо зверху
MC_SKY_BOT      = (155, 198, 247)   # небо знизу
MC_YELLOW       = (255, 213, 0)     # жовтий (як золото в MC)
MC_WHITE        = (255, 255, 255)
MC_SHADOW       = (60, 40, 20)      # коричнева тінь тексту
MC_GRID         = (0, 0, 0, 30)     # напівпрозора сітка блоків

BLOCK = 32  # розмір одного блоку в пікселях


# ── Піксельна сітка ───────────────────────────────────────────────────────────

def _draw_block_grid(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """Малює піксельну сітку блоків як у Minecraft."""
    for x in range(0, w, BLOCK):
        draw.line([(x, 0), (x, h)], fill=(0, 0, 0, 25), width=1)
    for y in range(0, h, BLOCK):
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, 25), width=1)


# ── Генерація фону ────────────────────────────────────────────────────────────

def _make_minecraft_bg(w: int, h: int) -> Image.Image:
    """
    Будує Minecraft-пейзаж:
      - верхні ~40% — небо з градієнтом
      - ~2 рядки блоків трави (зелений верх + бік)
      - решта — шари землі і каменю з текстурою
    """
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img, "RGBA")

    grass_y = int(h * 0.38)       # де починається трава
    grass_h = BLOCK               # висота шару трави
    dirt_y  = grass_y + grass_h  # де починається земля

    # ── Небо ─────────────────────────────────────────────────────────────────
    for y in range(grass_y):
        t = y / max(grass_y - 1, 1)
        r = int(MC_SKY_TOP[0] + (MC_SKY_BOT[0] - MC_SKY_TOP[0]) * t)
        g = int(MC_SKY_TOP[1] + (MC_SKY_BOT[1] - MC_SKY_TOP[1]) * t)
        b = int(MC_SKY_TOP[2] + (MC_SKY_BOT[2] - MC_SKY_TOP[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # ── Блоки трави по ширині ─────────────────────────────────────────────────
    rng = random.Random(42)  # фіксований seed — однакова текстура щоразу
    for bx in range(0, w, BLOCK):
        # Верх блоку трави (яскраво-зелений) з легкими варіаціями
        for y in range(grass_y, grass_y + BLOCK // 3):
            shade = rng.randint(-8, 8)
            c = tuple(max(0, min(255, v + shade)) for v in MC_GRASS_TOP)
            draw.line([(bx, y), (min(bx + BLOCK - 1, w), y)], fill=c)
        # Бік блоку трави
        for y in range(grass_y + BLOCK // 3, grass_y + grass_h):
            shade = rng.randint(-6, 6)
            c = tuple(max(0, min(255, v + shade)) for v in MC_GRASS_SIDE)
            draw.line([(bx, y), (min(bx + BLOCK - 1, w), y)], fill=c)

    # ── Шари землі і каменю ───────────────────────────────────────────────────
    for y in range(dirt_y, h):
        depth = y - dirt_y
        # Перші 2 рядки блоків — земля, далі — камінь
        if depth < BLOCK * 2:
            base = MC_DIRT if (y // 4 + 0) % 2 == 0 else MC_DIRT_DARK
        else:
            base = MC_STONE if (y // 4) % 2 == 0 else MC_STONE_DARK
        shade = rng.randint(-10, 10)
        c = tuple(max(0, min(255, v + shade)) for v in base)
        draw.line([(0, y), (w, y)], fill=c)

    # ── Вертикальні роздільники блоків (темні лінії) ─────────────────────────
    for bx in range(0, w, BLOCK):
        draw.line([(bx, grass_y), (bx, h)], fill=(0, 0, 0, 60), width=1)
    for by in range(grass_y, h, BLOCK):
        draw.line([(0, by), (w, by)], fill=(0, 0, 0, 60), width=1)

    # ── Піксельна сітка на небі ───────────────────────────────────────────────
    for bx in range(0, w, BLOCK):
        draw.line([(bx, 0), (bx, grass_y)], fill=(255, 255, 255, 15), width=1)
    for by in range(0, grass_y, BLOCK):
        draw.line([(0, by), (w, by)], fill=(255, 255, 255, 15), width=1)

    return img


# ── Шрифт ─────────────────────────────────────────────────────────────────────

def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


# ── Піксельний текст з тінню ──────────────────────────────────────────────────

def _draw_mc_text(draw: ImageDraw.ImageDraw, text: str, x: int, y: int,
                  font, color: tuple, shadow_offset: int = 3) -> None:
    """Малює текст з коричневою Minecraft-тінню (зміщення вправо-вниз)."""
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font,
              fill=MC_SHADOW, align="center")
    draw.text((x, y), text, font=font, fill=color, align="center")


# ── Публічний API ─────────────────────────────────────────────────────────────

def generate_vacancy_image(title: str, num: int = 0) -> io.BytesIO:
    """Генерує PNG у стилі Minecraft. Повертає BytesIO для Telegram."""
    img  = _make_minecraft_bg(IMG_W, IMG_H)
    draw = ImageDraw.Draw(img, "RGBA")

    # Напівпрозора темна плашка для тексту (як знак у MC)
    pad = 24
    sign_top    = 55 if num else 70
    sign_bottom = IMG_H - 30
    draw.rectangle(
        [pad, sign_top, IMG_W - pad, sign_bottom],
        fill=(30, 20, 10, 170),
        outline=(60, 40, 20, 200),
        width=3,
    )

    # Номер вакансії — жовтий угорі
    if num:
        font_num = _get_font(20)
        num_text = f"Вакансія #{num}"
        nb = draw.textbbox((0, 0), num_text, font=font_num)
        nx = (IMG_W - (nb[2] - nb[0])) // 2
        _draw_mc_text(draw, num_text, nx, sign_top + 10, font_num, MC_YELLOW, shadow_offset=2)

    # Назва вакансії — білий текст по центру
    font_title = _get_font(46)
    wrapped = textwrap.fill(title, width=WRAP_WIDTH)
    tb = draw.textbbox((0, 0), wrapped, font=font_title)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = (IMG_W - tw) // 2
    ty_center = (sign_top + sign_bottom - th) // 2 + (12 if num else 0)
    _draw_mc_text(draw, wrapped, tx, ty_center, font_title, MC_WHITE, shadow_offset=3)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf