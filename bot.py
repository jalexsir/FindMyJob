"""
Telegram-бот: перегляд вакансій з Hide/Unhide та очищенням листування.
HIDDEN_VACANCIES зберігається в bot_data["hidden_{user_id}"] — per-user.
"""

import html
import logging
import os
import re
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto,
    KeyboardButton, ReplyKeyboardMarkup, Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from feeds import fetch_all_vacancies, make_vacancy_hash, MergedSource, Vacancy, AVAILABLE_CATEGORIES
from image_gen import generate_vacancy_image

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAX_PER_SOURCE = 20

# ── Callback-константи ────────────────────────────────────────────────────────
CB_VAC_1D        = "vacancies_1d"
CB_VAC_14D       = "vacancies_14d"
CB_CONFIRM_YES   = "confirm_yes:"
CB_CONFIRM_NO    = "confirm_no"
CB_SHOW_HIDDEN   = "show_hidden"
CB_CLEAR         = "clear_history"
CB_CLEAR_HIDE    = "clear_hidden"
CB_HIDE          = "hide:"
CB_UNHIDE        = "unhide:"
CB_RESTORE       = "restore:"
CB_FAVORITE      = "fav:"
CB_UNFAVORITE    = "unfav:"
CB_FAV_DELETE    = "fav_del:"      # видалити з обраних при перегляді списку
CB_SHOW_FAVS     = "show_favorites"
CB_FAVS_YES      = "favs_yes"
CB_FAVS_NO       = "favs_no"
CB_CAT_TOGGLE    = "cat_toggle:"    # cat_toggle:<категорія>
CB_CAT_CONFIRM   = "cat_confirm"    # підтвердити вибір категорій
CB_RESELECT_CATS = "reselect_cats"  # повернутися до вибору категорій

# ── Тексти кнопок Reply Keyboard ──────────────────────────────────────────────
BTN_VAC_1D      = "📅 Вакансії за 1 день"
BTN_VAC_14D     = "📆 Вакансії за 7 днів"
BTN_VAC_ALL     = "📋 Всі вакансії"
BTN_SHOW_HIDDEN = "🔍 Вилучені з пошуку"
BTN_FAVORITES   = "⭐ Обране"
BTN_CLEAR       = "🗑 Очистити листування"
BTN_CLEAR_HIDE  = "🗑 Очистити список вилучених"

ALL_BTN_TEXTS = [BTN_VAC_1D, BTN_VAC_14D, BTN_VAC_ALL, BTN_SHOW_HIDDEN, BTN_FAVORITES, BTN_CLEAR, BTN_CLEAR_HIDE]


# ── Per-user сховище HIDDEN ───────────────────────────────────────────────────

def _hidden_key(update: Update) -> str:
    return f"hidden_{update.effective_user.id}"

def get_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update) -> dict[str, str]:
    return context.bot_data.setdefault(_hidden_key(update), {})

def set_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update, data: dict) -> None:
    context.bot_data[_hidden_key(update)] = data


# ── Per-user сховище FAVORITES ────────────────────────────────────────────────

def _fav_key(user_id: int) -> str:
    return f"favorites_{user_id}"

def get_favorites(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict[str, dict]:
    """Повертає {short_link: vacancy_dict} обраних вакансій юзера."""
    return context.bot_data.setdefault(_fav_key(user_id), {})

def set_favorites(context: ContextTypes.DEFAULT_TYPE, user_id: int, data: dict) -> None:
    context.bot_data[_fav_key(user_id)] = data


# ── Per-user seen-хеші (пам'ять переглянутих вакансій) ───────────────────────

def _seen_key(user_id: int) -> str:
    return f"seen_{user_id}"

def get_seen(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> set[str]:
    return context.bot_data.setdefault(_seen_key(user_id), set())

def add_to_seen(context: ContextTypes.DEFAULT_TYPE, user_id: int, hashes: list[str]) -> None:
    seen = get_seen(context, user_id)
    seen.update(hashes)
    context.bot_data[_seen_key(user_id)] = seen


# ── Per-user вибрані категорії ────────────────────────────────────────────────

def _cat_key(user_id: int) -> str:
    return f"categories_{user_id}"

def get_user_categories(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> list[str]:
    return context.bot_data.get(_cat_key(user_id), [])

def set_user_categories(context: ContextTypes.DEFAULT_TYPE, user_id: int, cats: list[str]) -> None:
    context.bot_data[_cat_key(user_id)] = cats


def build_category_keyboard(selected: list[str]) -> InlineKeyboardMarkup:
    """Клавіатура вибору категорій — по 2 в рядку, кнопка підтвердження окремо."""
    cats = AVAILABLE_CATEGORIES
    rows = []
    # По 2 категорії в рядку
    for i in range(0, len(cats), 2):
        row = []
        for cat in cats[i:i+2]:
            mark = "✅" if cat in selected else "⬜"
            row.append(InlineKeyboardButton(
                f"{mark} {cat}",
                callback_data=f"{CB_CAT_TOGGLE}{cat}",
            ))
        rows.append(row)
    # Кнопка підтвердження — тільки якщо щось обрано
    if selected:
        rows.append([InlineKeyboardButton(
            f"▶️ Продовжити ({len(selected)} обрано)",
            callback_data=CB_CAT_CONFIRM,
        )])
    return InlineKeyboardMarkup(rows)


def category_status_text(selected: list[str]) -> str:
    if not selected:
        return "Оберіть одну або декілька категорій вакансій 👇"
    cats_str = ", ".join(selected)
    return f"Ви обрали категорій: <b>{cats_str}</b>\n\nНатисни ▶️ Продовжити або обери ще."




def track(context: ContextTypes.DEFAULT_TYPE, *ids: int) -> None:
    context.chat_data.setdefault("message_ids", []).extend(ids)


# ── Клавіатури ────────────────────────────────────────────────────────────────

def build_persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_VAC_1D), KeyboardButton(BTN_VAC_14D), KeyboardButton(BTN_VAC_ALL)],
            [KeyboardButton(BTN_SHOW_HIDDEN), KeyboardButton(BTN_FAVORITES)],
            [KeyboardButton(BTN_CLEAR), KeyboardButton(BTN_CLEAR_HIDE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def build_vacancy_keyboard(vacancy: Vacancy, is_favorite: bool = False) -> InlineKeyboardMarkup:
    short_link = vacancy.link[:50]
    fav_btn = InlineKeyboardButton(
        "💛 В обраному" if is_favorite else "⭐ В обране",
        callback_data=f"{CB_UNFAVORITE}{short_link}" if is_favorite else f"{CB_FAVORITE}{short_link}",
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Відкрити вакансію", url=vacancy.link)],
        [fav_btn, InlineKeyboardButton("🙈 Не показувати", callback_data=f"{CB_HIDE}{short_link}")],
    ])

def build_restore_keyboard(short_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Відновити вакансію", callback_data=f"{CB_RESTORE}{short_link}")],
    ])

def build_favorite_vacancy_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    """Клавіатура для вакансії зі списку Обраних — з кнопкою видалення."""
    short_link = vacancy.link[:50]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Відкрити вакансію", url=vacancy.link)],
        [InlineKeyboardButton("🗑 Видалити з обраних", callback_data=f"{CB_FAV_DELETE}{short_link}")],
        [InlineKeyboardButton("🚫 Вилучити з пошуку", callback_data=f"{CB_HIDE}{short_link}")],
    ])


# ── Форматування ──────────────────────────────────────────────────────────────

def clean_salary(raw: str) -> str:
    if not raw:
        return "не вказано"
    text = html.unescape(raw)
    text = re.sub(r'[\s\xa0]+', ' ', text).strip()
    has_range = bool(re.search(r'\d[\s]*[–\-][\s]*\d', text))
    if not has_range:
        text = re.sub(r'\bвід\b\s*', '', text, flags=re.IGNORECASE).strip()
    return text or "не вказано"

UA_MONTHS = {
    1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
    5: "травня", 6: "червня", 7: "липня", 8: "серпня",
    9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня",
}

def format_date_ua(published: str) -> str:
    """Перетворює дату будь-якого формату на '23.06.2026'."""
    from email.utils import parsedate_to_datetime as _ptd
    d = None
    try:
        d = datetime.strptime(published, "%d.%m.%Y")
    except (ValueError, TypeError):
        pass
    if d is None:
        try:
            d = _ptd(published)
        except Exception:
            pass
    if d is None:
        return published
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def format_vacancy(vacancy: Vacancy, num: int = 0) -> str:
    salary = clean_salary(vacancy.salary)
    lines = []
    lines.append(f"💼 <b>Спеціальність:</b> {html.escape(vacancy.title)}")
    if vacancy.company:
        lines.append(f"🏢 <b>Компанія:</b> {html.escape(vacancy.company)}")
    if vacancy.location:
        lines.append(f"📍 <b>Місце роботи:</b> {html.escape(vacancy.location)}")
    if salary and salary != "не вказано":
        lines.append(f"💰 <b>Зарплата:</b> {html.escape(salary)}")
    lines.append(f"📅 <b>Дата публікації:</b> {html.escape(format_date_ua(vacancy.published))}")
    lines.append(f"🌐 <b>Сайт:</b> {html.escape(vacancy.source)}")
    return "\n".join(lines)

def build_summary(sources: list[MergedSource], days: int | None) -> str:
    total = sum(len(ms.vacancies) for ms in sources)
    period = f"за останні {days} д." if days else "за весь час"
    lines = [f"🗂 <b>{period.capitalize()} знайдено {total} вакансій:</b>\n"]
    for ms in sources:
        dup = f" (видалено {ms.duplicates} дублікатів)" if ms.duplicates else ""
        lines.append(f"  • {ms.name} — {len(ms.vacancies)} вак.{dup}")
    return "\n".join(lines)


# ── Надсилання вакансій ───────────────────────────────────────────────────────

async def _send_vacancies(chat, pending: list[dict], context: ContextTypes.DEFAULT_TYPE,
                          user_id: int, force_favorite: bool = False,
                          from_favorites: bool = False) -> list[int]:
    """Надсилає список вакансій. Нові позначаються NEW, обрані — зіркою."""
    seen = get_seen(context, user_id)
    favorites = get_favorites(context, user_id)
    ids = []
    new_hashes = []

    for i, vd in enumerate(pending, 1):
        v = Vacancy(**vd)
        vh = v.vacancy_hash or make_vacancy_hash(v.title, v.company)
        is_new = vh not in seen
        is_fav = force_favorite or (v.link[:50] in favorites)
        if is_new:
            new_hashes.append(vh)

        image = generate_vacancy_image(v.title, i, is_new=is_new, is_favorite=is_fav)
        kb = build_favorite_vacancy_keyboard(v) if from_favorites else build_vacancy_keyboard(v, is_favorite=is_fav)
        msg = await chat.send_photo(
            photo=image,
            caption=format_vacancy(v, i),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        ids.append(msg.message_id)

    if new_hashes:
        add_to_seen(context, user_id, new_hashes)

    return ids


# ── Обробники: /start та Reply Keyboard ───────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    context.chat_data["start_message_id"] = update.message.message_id
    set_user_categories(context, user_id, [])

    msg = await update.message.reply_text(
        category_status_text([]),
        parse_mode=ParseMode.HTML,
        reply_markup=build_category_keyboard([]),
    )
    track(context, msg.message_id)


async def toggle_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    cat = query.data[len(CB_CAT_TOGGLE):]
    selected = get_user_categories(context, user_id)
    if cat in selected:
        selected.remove(cat)
    else:
        selected.append(cat)
    set_user_categories(context, user_id, selected)
    await query.edit_message_text(
        category_status_text(selected),
        parse_mode=ParseMode.HTML,
        reply_markup=build_category_keyboard(selected),
    )


async def confirm_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    selected = get_user_categories(context, user_id)
    if not selected:
        await query.answer("Оберіть хоча б одну категорію!", show_alert=True)
        return
    cats_str = ", ".join(selected)
    await query.edit_message_text(
        f"✅ Категорії обрано: <b>{cats_str}</b>\n\nМеню доступне внизу 👇",
        parse_mode=ParseMode.HTML,
    )
    msg = await query.message.chat.send_message(
        "Натисни кнопку меню внизу щоб обрати дію.",
        reply_markup=build_persistent_keyboard(),
    )
    track(context, msg.message_id)


async def track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        track(context, update.message.message_id)


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    track(context, update.message.message_id)
    if text == BTN_VAC_1D:
        await _request_vacancies(update.message, context, days=1, user_id=update.effective_user.id)
    elif text == BTN_VAC_14D:
        await _request_vacancies(update.message, context, days=7, user_id=update.effective_user.id)
    elif text == BTN_VAC_ALL:
        await _request_vacancies(update.message, context, days=None, user_id=update.effective_user.id)
    elif text == BTN_SHOW_HIDDEN:
        await _show_hidden(update.message.chat, context, update)
    elif text == BTN_FAVORITES:
        await show_favorites_prompt(update.message.chat, context, update.effective_user.id)
    elif text == BTN_CLEAR:
        await _do_clear_history(
            update.message.chat_id, update.message.message_id, context,
            user_id=update.effective_user.id,
        )
    elif text == BTN_CLEAR_HIDE:
        await _do_clear_hidden(update.message.chat, context, update)


# ── Логіка перегляду вакансій ─────────────────────────────────────────────────

async def _request_vacancies(message, context: ContextTypes.DEFAULT_TYPE, days: int | None,
                             user_id: int = 0) -> None:
    """Завантажує вакансії, показує зведення + питання Так/Ні."""
    wait_msg = await message.reply_text("⏳ Завантажую вакансії, зачекай...")
    track(context, wait_msg.message_id)

    user_cats = get_user_categories(context, user_id)
    all_sources = fetch_all_vacancies(days=days, categories=user_cats or None)
    filtered = all_sources
    hidden = context.bot_data.get(f"hidden_{message.chat.id}", {}) if hasattr(message, 'chat') else {}

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)

    context.chat_data["pending_vacancies"] = [
        {"source": v.source, "title": v.title, "link": v.link,
         "published": v.published, "pub_dt": v.pub_dt,
         "company": v.company, "location": v.location, "salary": v.salary,
         "vacancy_hash": v.vacancy_hash}
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    summary = build_summary(filtered, days)

    # Видаляємо "зачекай..." і відправляємо результат новим повідомленням
    try:
        await context.bot.delete_message(chat_id=message.chat_id, message_id=wait_msg.message_id)
    except Exception:
        pass

    if total == 0:
        msg = await message.reply_text(
            summary + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
        )
        track(context, msg.message_id)
        return

    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
            InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days if days is not None else 'all'}"),
        ],
        [InlineKeyboardButton("🔄 Переобрати категорії пошуку", callback_data=CB_RESELECT_CATS)],
    ])
    msg = await message.reply_text(
        summary + "\n\n❓ <b>Показати вакансії?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb,
    )
    track(context, msg.message_id)


async def show_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    """Callback-версія запиту вакансій (через inline меню)."""
    query = update.callback_query
    await query.answer()
    track(context, query.message.message_id)
    await query.edit_message_text("⏳ Завантажую вакансії, зачекай...")

    user_cats = get_user_categories(context, update.effective_user.id if update.effective_user else 0)
    all_sources = fetch_all_vacancies(days=days, categories=user_cats or None)
    filtered = all_sources
    hidden = get_hidden(context, update)

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)
    summary = build_summary(filtered, days)

    context.chat_data["pending_vacancies"] = [
        {"source": v.source, "title": v.title, "link": v.link,
         "published": v.published, "pub_dt": v.pub_dt,
         "company": v.company, "location": v.location, "salary": v.salary,
         "vacancy_hash": v.vacancy_hash}
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    if total == 0:
        await query.edit_message_text(
            summary + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
        )
        return

    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
            InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days if days is not None else 'all'}"),
        ],
        [InlineKeyboardButton("🔄 Переобрати категорії пошуку", callback_data=CB_RESELECT_CATS)],
    ])
    await query.edit_message_text(
        summary + "\n\n❓ <b>Показати вакансії?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb,
    )


async def vacancies_1d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=1)

async def vacancies_14d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=14)


async def reselect_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Повертає до діалогу вибору категорій зі збереженим поточним вибором."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    selected = get_user_categories(context, user_id)
    # Очищаємо pending — при новому виборі категорій вакансії будуть перезавантажені
    context.chat_data.pop("pending_vacancies", None)
    await query.edit_message_text(
        category_status_text(selected),
        parse_mode=ParseMode.HTML,
        reply_markup=build_category_keyboard(selected),
    )


async def confirm_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Крок 2: завантажує і виводить вакансії після підтвердження 'Так'."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    # Витягуємо days з callback_data: "confirm_yes:1", "confirm_yes:7", "confirm_yes:all"
    raw_days = query.data[len(CB_CONFIRM_YES):]
    days: int | None = None if raw_days == "all" else int(raw_days)

    user_id = query.from_user.id
    user_cats = get_user_categories(context, user_id)
    hidden = get_hidden(context, update)

    wait_msg = await query.message.reply_text("⏳ Завантажую вакансії, зачекай...")
    track(context, wait_msg.message_id)

    all_sources = fetch_all_vacancies(days=days, categories=user_cats or None)

    for ms in all_sources:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    pending = [
        {"source": v.source, "title": v.title, "link": v.link,
         "published": v.published, "pub_dt": v.pub_dt,
         "company": v.company, "location": v.location,
         "salary": v.salary, "vacancy_hash": v.vacancy_hash}
        for ms in all_sources for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    try:
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=wait_msg.message_id)
    except Exception:
        pass

    if not pending:
        msg = await query.message.reply_text("Вакансій не знайдено (або всі приховані).")
        track(context, msg.message_id)
        return

    ids = await _send_vacancies(query.message.chat, pending, context, user_id)
    track(context, *ids)
    msg = await query.message.chat.send_message(
        "✅ Всі доступні запитані вакансії доступні для перегляду вище.",
    )
    track(context, msg.message_id)


async def confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.chat_data.pop("pending_vacancies", None)
    await query.edit_message_reply_markup(reply_markup=None)
    msg = await query.message.reply_text("👍 Добре, почекаємо оновлень!")
    track(context, msg.message_id)


# ── Hide / Unhide ──────────────────────────────────────────────────────────────

async def hide_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    short_link = query.data[len(CB_HIDE):]
    hidden = get_hidden(context, update)

    caption = query.message.caption or ""
    title = next(
        (line.replace("💼", "").replace("Спеціальність:", "").strip()
         for line in caption.split("\n") if "Спеціальність:" in line),
        "вакансія"
    )

    hidden[short_link] = title
    set_hidden(context, update, hidden)

    await query.edit_message_caption(
        caption=f"🙈 <b>Приховано:</b> {html.escape(title)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👁 Відновити до перегляду", callback_data=f"{CB_UNHIDE}{short_link}")
        ]]),
    )


async def unhide_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    short_link = query.data[len(CB_UNHIDE):]
    hidden = get_hidden(context, update)
    title = hidden.pop(short_link, "вакансія")
    set_hidden(context, update, hidden)

    all_sources = fetch_all_vacancies()
    found = next(
        (v for ms in all_sources for v in ms.vacancies if v.link[:50] == short_link),
        None,
    )

    if found:
        image = generate_vacancy_image(found.title, 0)
        await query.message.edit_media(
            media=InputMediaPhoto(
                media=image,
                caption=format_vacancy(found),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_vacancy_keyboard(found),
        )
    else:
        await query.edit_message_caption(
            caption=f"👁 <b>{html.escape(title)}</b>\n\nВакансія відновлена. Оновіть список для деталей.",
            parse_mode=ParseMode.HTML,
        )


async def restore_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("✅ Вакансію відновлено")
    hidden = get_hidden(context, update)
    hidden.pop(query.data[len(CB_RESTORE):], None)
    set_hidden(context, update, hidden)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


# ── Список прихованих ─────────────────────────────────────────────────────────

async def _show_hidden(chat, context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    hidden = get_hidden(context, update)
    if not hidden:
        msg = await chat.send_message("📭 Список прихованих вакансій порожній.")
        track(context, msg.message_id)
        return

    msg = await chat.send_message(
        f"🙈 <b>Приховані вакансії ({len(hidden)}):</b>",
        parse_mode=ParseMode.HTML,
    )
    track(context, msg.message_id)
    for short_link, title in hidden.items():
        msg = await chat.send_message(
            f"🙈 <b>{html.escape(title)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_restore_keyboard(short_link),
        )
        track(context, msg.message_id)


async def show_hidden_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _show_hidden(query.message.chat, context, update)


# ── Очищення ──────────────────────────────────────────────────────────────────

async def add_to_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Додає вакансію в обране."""
    query = update.callback_query
    await query.answer("⭐ Додано в обране!")
    short_link = query.data[len(CB_FAVORITE):]
    user_id = query.from_user.id
    favs = get_favorites(context, user_id)

    # Зберігаємо дані вакансії з caption
    caption = query.message.caption or ""
    title = next(
        (line.replace("💼", "").replace("Спеціальність:", "").strip()
         for line in caption.split("\n") if "Спеціальність:" in line),
        "вакансія"
    )
    favs[short_link] = {"title": title, "short_link": short_link}
    set_favorites(context, user_id, favs)

    # Оновлюємо кнопку на "В обраному"
    try:
        kb = query.message.reply_markup.inline_keyboard
        new_kb = []
        for row in kb:
            new_row = []
            for btn in row:
                if btn.callback_data == f"{CB_FAVORITE}{short_link}":
                    new_row.append(InlineKeyboardButton(
                        "💛 В обраному", callback_data=f"{CB_UNFAVORITE}{short_link}"
                    ))
                else:
                    new_row.append(btn)
            new_kb.append(new_row)
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))
    except Exception:
        pass


async def remove_from_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видаляє вакансію з обраного."""
    query = update.callback_query
    await query.answer("💔 Видалено з обраного")
    short_link = query.data[len(CB_UNFAVORITE):]
    user_id = query.from_user.id
    favs = get_favorites(context, user_id)
    favs.pop(short_link, None)
    set_favorites(context, user_id, favs)

    try:
        kb = query.message.reply_markup.inline_keyboard
        new_kb = []
        for row in kb:
            new_row = []
            for btn in row:
                if btn.callback_data == f"{CB_UNFAVORITE}{short_link}":
                    new_row.append(InlineKeyboardButton(
                        "⭐ Додати в обране", callback_data=f"{CB_FAVORITE}{short_link}"
                    ))
                else:
                    new_row.append(btn)
            new_kb.append(new_row)
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_kb))
    except Exception:
        pass


async def show_favorites_prompt(chat, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Показує зведення обраних і питає Так/Ні."""
    favs = get_favorites(context, user_id)
    count = len(favs)
    if count == 0:
        msg = await chat.send_message("⭐ Список обраних вакансій порожній.")
        track(context, msg.message_id)
        return

    confirm_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Ні",  callback_data=CB_FAVS_NO),
        InlineKeyboardButton("✅ Так", callback_data=CB_FAVS_YES),
    ]])
    msg = await chat.send_message(
        f"⭐ <b>У списку Обраних вакансій є {count} вак.</b>\n\n❓ Показати їх?",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb,
    )
    track(context, msg.message_id)


async def favs_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує всі обрані вакансії."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    user_id = query.from_user.id
    favs = get_favorites(context, user_id)
    if not favs:
        msg = await query.message.reply_text("⭐ Список обраних порожній.")
        track(context, msg.message_id)
        return

    # Завантажуємо всі вакансії і фільтруємо по short_link
    all_sources = fetch_all_vacancies(days=None)
    fav_vacancies = []
    for ms in all_sources:
        for v in ms.vacancies:
            if v.link[:50] in favs:
                fav_vacancies.append({
                    "source": v.source, "title": v.title, "link": v.link,
                    "published": v.published, "pub_dt": v.pub_dt,
                    "company": v.company, "location": v.location,
                    "salary": v.salary, "vacancy_hash": v.vacancy_hash,
                })

    if not fav_vacancies:
        msg = await query.message.reply_text(
            "⭐ Обрані вакансії не знайдені в поточних джерелах (можливо, вже зняті з публікації)."
        )
        track(context, msg.message_id)
        return

    ids = await _send_vacancies(
        query.message.chat, fav_vacancies, context, user_id,
        force_favorite=True, from_favorites=True,
    )
    track(context, *ids)
    msg = await query.message.chat.send_message(
        f"✅ Показано {len(fav_vacancies)} обраних вакансій."
    )
    track(context, msg.message_id)


async def favs_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    msg = await query.message.reply_text("⏰ Часікі тікають!")
    track(context, msg.message_id)


async def fav_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видаляє вакансію з обраних при перегляді списку."""
    query = update.callback_query
    await query.answer("🗑 Видалено з обраних")
    short_link = query.data[len(CB_FAV_DELETE):]
    favs = get_favorites(context, query.from_user.id)
    favs.pop(short_link, None)
    set_favorites(context, query.from_user.id, favs)
    # Залишаємо тільки кнопку "Відкрити вакансію"
    try:
        open_btn = query.message.reply_markup.inline_keyboard[0][0]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([[open_btn]]))
    except Exception:
        await query.edit_message_reply_markup(reply_markup=None)



    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    msg = await query.message.reply_text("⏰ Часікі тікають!")
    track(context, msg.message_id)


async def _do_clear_history(chat_id: int, current_id: int, context: ContextTypes.DEFAULT_TYPE,
                            user_id: int = 0) -> None:
    track(context, current_id)
    saved_ids = context.chat_data.get("message_ids", [])
    min_id = min(saved_ids) if saved_ids else current_id
    start_id = context.chat_data.get("start_message_id")
    context.chat_data["message_ids"] = []

    deleted = 0
    for mid in range(min_id, current_id + 1):
        if mid == start_id:
            continue
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass

    # Скидаємо категорії юзера і повертаємось до вибору
    if user_id:
        set_user_categories(context, user_id, [])

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=category_status_text([]),
        parse_mode=ParseMode.HTML,
        reply_markup=build_category_keyboard([]),
    )
    track(context, msg.message_id)


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🗑 Очищую...")
    await _do_clear_history(
        query.message.chat_id, query.message.message_id, context,
        user_id=query.from_user.id,
    )


async def _do_clear_hidden(chat, context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    hidden = get_hidden(context, update)
    count = len(hidden)
    if count == 0:
        text = "📭 Список пустий"
    else:
        set_hidden(context, update, {})
        text = f"✅ Список вилучених вакансій очищений ({count})"
    msg = await chat.send_message(text)
    track(context, msg.message_id)


async def clear_hidden(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _do_clear_hidden(query.message.chat, context, update)


# ── Запуск ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не знайдено. Створи .env з BOT_TOKEN=...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(toggle_category,       pattern=f"^{CB_CAT_TOGGLE}"))
    app.add_handler(CallbackQueryHandler(confirm_categories,    pattern=f"^{CB_CAT_CONFIRM}$"))
    app.add_handler(CallbackQueryHandler(reselect_categories,   pattern=f"^{CB_RESELECT_CATS}$"))
    app.add_handler(CallbackQueryHandler(vacancies_1d,    pattern=f"^{CB_VAC_1D}$"))
    app.add_handler(CallbackQueryHandler(vacancies_14d,   pattern=f"^{CB_VAC_14D}$"))
    app.add_handler(CallbackQueryHandler(confirm_show,    pattern=f"^{CB_CONFIRM_YES}"))
    app.add_handler(CallbackQueryHandler(confirm_no,      pattern=f"^{CB_CONFIRM_NO}$"))
    app.add_handler(CallbackQueryHandler(show_hidden_list,pattern=f"^{CB_SHOW_HIDDEN}$"))
    app.add_handler(CallbackQueryHandler(clear_history,   pattern=f"^{CB_CLEAR}$"))
    app.add_handler(CallbackQueryHandler(clear_hidden,    pattern=f"^{CB_CLEAR_HIDE}$"))
    app.add_handler(CallbackQueryHandler(hide_vacancy,          pattern=f"^{CB_HIDE}"))
    app.add_handler(CallbackQueryHandler(unhide_vacancy,        pattern=f"^{CB_UNHIDE}"))
    app.add_handler(CallbackQueryHandler(restore_vacancy,       pattern=f"^{CB_RESTORE}"))
    app.add_handler(CallbackQueryHandler(add_to_favorites,      pattern=f"^{CB_FAVORITE}"))
    app.add_handler(CallbackQueryHandler(remove_from_favorites, pattern=f"^{CB_UNFAVORITE}"))
    app.add_handler(CallbackQueryHandler(fav_delete,            pattern=f"^{CB_FAV_DELETE}"))
    app.add_handler(CallbackQueryHandler(favs_yes,              pattern=f"^{CB_FAVS_YES}$"))
    app.add_handler(CallbackQueryHandler(favs_no,               pattern=f"^{CB_FAVS_NO}$"))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(f"^({'|'.join(re.escape(b) for b in ALL_BTN_TEXTS)})$"),
        handle_reply_button,
    ))
    app.add_handler(MessageHandler(filters.ALL, track_user_message))

    logger.info("Бот запущений. Ctrl+C для зупинки.")
    app.run_polling()


if __name__ == "__main__":
    main()