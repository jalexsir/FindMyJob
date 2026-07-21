"""
Minecraft-стиль: яскраве небо з піксельними хмарами,
острівці землі/трави внизу, жовтий/білий текст.
"""

import io
import textwrap
from PIL import Image, ImageDraw, ImageFont

IMG_W, IMG_H = 800, 300
WRAP_WIDTH    = 26
BLOCK         = 20   # розмір пікселя-блоку

# ── Яскрава палітра ───────────────────────────────────────────────────────────
SKY_TOP      = (30,  120, 255)   # яскраво-синій верх
SKY_BOT      = (100, 200, 255)   # блакитний низ неба
CLOUD_WHITE  = (255, 255, 255)
CLOUD_SHADOW = (200, 230, 255)
GRASS_TOP    = (80,  220,  40)   # кислотно-зелений
GRASS_SIDE   = (60,  180,  30)
DIRT_TOP     = (180, 110,  50)   # яскраво-коричневий
DIRT_BOT     = (140,  80,  30)
STONE        = (160, 160, 160)
GOLD         = (255, 215,   0)   # жовтий текст
WHITE        = (255, 255, 255)
TEXT_SHADOW  = ( 80,  40,   0)   # коричнева тінь

# ── Піксельні хмари (координати в блоках, (bx, by, width, height)) ───────────
CLOUDS = [
    (1,  1, 5, 2),
    (8,  2, 7, 2),
    (18, 1, 4, 2),
    (25, 2, 6, 2),
    (33, 1, 5, 2),
]

# ── Острівці (bx, by_blocks_from_bottom, width_blocks) ───────────────────────
# by — скільки блоків від низу картинки
ISLANDS = [
    # (x_start_px, y_top_px, width_px)
]  # заповнюємо програмно


def _sky_color(y: int) -> tuple:
    t = y / (IMG_H - 1)
    return tuple(int(SKY_TOP[i] + (SKY_BOT[i] - SKY_TOP[i]) * t) for i in range(3))


def _draw_pixel_rect(draw, x, y, w, h, fill, outline=None) -> None:
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill)
    if outline:
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=outline, width=2)


def _draw_tree(draw: ImageDraw.ImageDraw, x: int, ground_y: int) -> None:
    """Малює піксельне Minecraft-дерево: стовбур + квадратна крона."""
    b = BLOCK
    trunk_color  = (101, 67, 33)   # коричневий стовбур
    trunk_dark   = (80,  50, 20)
    leaves_color = (34, 180, 34)   # яскраво-зелена крона
    leaves_dark  = (20, 140, 20)
    leaves_light = (60, 210, 60)

    # Стовбур: 1 блок завширшки, 3 блоки завввишки
    for i in range(3):
        ty = ground_y - (i + 1) * b
        c = trunk_dark if i == 0 else trunk_color
        draw.rectangle([x, ty, x + b - 1, ty + b - 1], fill=c, outline=(0,0,0), width=1)

    # Крона: 3×3 блоки, центрована над стовбуром
    crown_top = ground_y - 3 * b - 3 * b
    for row in range(3):
        for col in range(3):
            cx = x - b + col * b
            cy = crown_top + row * b
            # Кути крони трохи темніші
            if (row == 0 and col in (0, 2)) or (row == 2 and col in (0, 2)):
                c = leaves_dark
            elif row == 0 and col == 1:
                c = leaves_light
            else:
                c = leaves_color
            draw.rectangle([cx, cy, cx + b - 1, cy + b - 1], fill=c, outline=(0,0,0), width=1)


def _make_bg() -> Image.Image:
    img = Image.new("RGB", (IMG_W, IMG_H))
    draw = ImageDraw.Draw(img)

    # ── Небо-градієнт ─────────────────────────────────────────────────────────
    for y in range(IMG_H):
        draw.line([(0, y), (IMG_W, y)], fill=_sky_color(y))

    # ── Піксельні хмари ───────────────────────────────────────────────────────
    for (cbx, cby, cw, ch) in CLOUDS:
        x0 = cbx * BLOCK
        y0 = cby * BLOCK
        px = BLOCK
        for dy in range(ch):
            for dx in range(cw):
                c = CLOUD_SHADOW if dy == ch - 1 else CLOUD_WHITE
                draw.rectangle(
                    [x0 + dx*px, y0 + dy*px,
                     x0 + dx*px + px - 1, y0 + dy*px + px - 1],
                    fill=c,
                )

    # ── Острівці землі та трави ───────────────────────────────────────────────
    islands_px = [
        (0,        IMG_H - BLOCK*4, BLOCK*14),
        (BLOCK*17, IMG_H - BLOCK*3, BLOCK*23),
    ]

    for (ix, iy, iw) in islands_px:
        grass_h = BLOCK
        dirt_h  = IMG_H - iy - grass_h
        mid_y   = iy + grass_h + (dirt_h // 2)

        draw.rectangle([ix, iy,          ix + iw - 1, iy + grass_h//2 - 1], fill=GRASS_TOP)
        draw.rectangle([ix, iy + grass_h//2, ix + iw - 1, iy + grass_h - 1], fill=GRASS_SIDE)
        draw.rectangle([ix, iy + grass_h, ix + iw - 1, mid_y - 1], fill=DIRT_TOP)
        draw.rectangle([ix, mid_y,        ix + iw - 1, IMG_H - 1],  fill=DIRT_BOT)

        for bx in range(ix, ix + iw, BLOCK):
            draw.line([(bx, iy), (bx, IMG_H)], fill=(0,0,0), width=1)
        for by in range(iy, IMG_H, BLOCK):
            draw.line([(ix, by), (ix + iw, by)], fill=(0,0,0), width=1)
        draw.rectangle([ix, iy, ix + iw - 1, IMG_H - 1], outline=(0,0,0), width=2)

    # ── Дерева (на вершині острівців) ─────────────────────────────────────────
    # Дерево 1: на першому острівці (x=0, iy=IMG_H-BLOCK*4), ставимо правіше центру
    island1_iy = IMG_H - BLOCK * 4
    _draw_tree(draw, x=BLOCK * 5, ground_y=island1_iy)

    # Дерево 2: на другому острівці (x=BLOCK*17), ставимо ближче до правого краю
    island2_iy = IMG_H - BLOCK * 3
    _draw_tree(draw, x=BLOCK * 30, ground_y=island2_iy)

    # ── Піксельна сітка на небі ───────────────────────────────────────────────
    for bx in range(0, IMG_W, BLOCK):
        draw.line([(bx, 0), (bx, IMG_H)], fill=(200, 230, 255), width=1)
    for by in range(0, IMG_H, BLOCK):
        draw.line([(0, by), (IMG_W, by)], fill=(200, 230, 255), width=1)

    return img


def _get_font(size: int):
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


def _draw_mc_text(draw, text, x, y, font, color):
    # Товста тінь (3 пікселі)
    for ox, oy in [(3,3),(3,0),(0,3)]:
        draw.text((x+ox, y+oy), text, font=font, fill=TEXT_SHADOW, align="center")
    draw.text((x, y), text, font=font, fill=color, align="center")


def generate_vacancy_image(title: str, num: int = 0, is_new: bool = False, is_favorite: bool = False) -> io.BytesIO:
    img  = _make_bg()
    draw = ImageDraw.Draw(img)

    # Темна напівпрозора плашка для тексту
    pad = 20
    sign_y1 = 45 if num else 60
    sign_y2 = int(IMG_H * 0.72)

    # Малюємо плашку через окремий шар
    overlay = Image.new("RGBA", (IMG_W, IMG_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([pad, sign_y1, IMG_W-pad, sign_y2], fill=(10, 10, 40, 185))
    od.rectangle([pad, sign_y1, IMG_W-pad, sign_y2], outline=(255, 215, 0), width=3)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Плашка "NEW" у верхньому лівому куті ─────────────────────────────────
    if is_new:
        font_new = _get_font(27)
        new_text = "✦ NEW ✦"
        nb = draw.textbbox((0, 0), new_text, font=font_new)
        nw = nb[2] - nb[0]
        nh = nb[3] - nb[1]
        nx1 = pad + 2
        ny1 = sign_y1 + 4
        nx2 = nx1 + nw + 12
        ny2 = ny1 + nh + 8
        draw.rectangle([nx1, ny1, nx2, ny2], fill=(220, 30, 30))
        draw.rectangle([nx1, ny1, nx2, ny2], outline=(255, 100, 100), width=2)
        draw.text((nx1 + 6, ny1 + 4), new_text, font=font_new, fill=(255, 255, 255))

    # ── Мітка "Обране" жовтим текстом в самому низу зліва ───────────────────
    if is_favorite:
        font_fav = _get_font(24)
        fav_text = "* Обране *"
        fb = draw.textbbox((0, 0), fav_text, font=font_fav)
        fh = fb[3] - fb[1]
        _draw_mc_text(draw, fav_text, pad + 6, IMG_H - fh - 6, font_fav, GOLD)

    # Номер вакансії — жовтий
    if num:
        font_num = _get_font(30)  # 20 * 1.5
        num_text = f">> Вакансія #{num} <<"
        nb = draw.textbbox((0,0), num_text, font=font_num)
        nx = (IMG_W - (nb[2]-nb[0])) // 2
        _draw_mc_text(draw, num_text, nx, sign_y1 + 8, font_num, GOLD)

    # Назва — білий текст
    font_title = _get_font(46)
    wrapped = textwrap.fill(title, width=WRAP_WIDTH)
    tb = draw.textbbox((0,0), wrapped, font=font_title)
    tw, th = tb[2]-tb[0], tb[3]-tb[1]
    tx = (IMG_W - tw) // 2
    ty = (sign_y1 + sign_y2 - th) // 2 + (10 if num else 0)
    _draw_mc_text(draw, wrapped, tx, ty, font_title, WHITE)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf