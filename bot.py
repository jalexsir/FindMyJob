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

from feeds import fetch_all_vacancies, MergedSource, Vacancy
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
CB_VAC_1D      = "vacancies_1d"
CB_VAC_14D     = "vacancies_14d"
CB_CONFIRM_YES = "confirm_yes:"   # confirm_yes:<days>
CB_CONFIRM_NO  = "confirm_no"
CB_SHOW_HIDDEN = "show_hidden"
CB_CLEAR       = "clear_history"
CB_CLEAR_HIDE  = "clear_hidden"
CB_HIDE        = "hide:"          # hide:<short_link>
CB_UNHIDE      = "unhide:"        # unhide:<short_link>
CB_RESTORE     = "restore:"       # restore:<short_link>

# ── Тексти кнопок Reply Keyboard ──────────────────────────────────────────────
BTN_VAC_1D      = "📅 Вакансії за 1 день"
BTN_VAC_14D     = "📆 Вакансії за 14 днів"
BTN_SHOW_HIDDEN = "👁 Відновлені вакансії"
BTN_CLEAR       = "🗑 Очистити листування"
BTN_CLEAR_HIDE  = "🚫 Видалити HIDDEN"

ALL_BTN_TEXTS = [BTN_VAC_1D, BTN_VAC_14D, BTN_SHOW_HIDDEN, BTN_CLEAR, BTN_CLEAR_HIDE]


# ── Per-user сховище HIDDEN ───────────────────────────────────────────────────

def _hidden_key(update: Update) -> str:
    return f"hidden_{update.effective_user.id}"

def get_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update) -> dict[str, str]:
    return context.bot_data.setdefault(_hidden_key(update), {})

def set_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update, data: dict) -> None:
    context.bot_data[_hidden_key(update)] = data


# ── Трекінг повідомлень ───────────────────────────────────────────────────────

def track(context: ContextTypes.DEFAULT_TYPE, *ids: int) -> None:
    context.chat_data.setdefault("message_ids", []).extend(ids)


# ── Клавіатури ────────────────────────────────────────────────────────────────

def build_persistent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_VAC_1D), KeyboardButton(BTN_VAC_14D)],
            [KeyboardButton(BTN_SHOW_HIDDEN)],
            [KeyboardButton(BTN_CLEAR), KeyboardButton(BTN_CLEAR_HIDE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def build_vacancy_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    short_link = vacancy.link[:50]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Відкрити вакансію", url=vacancy.link)],
        [InlineKeyboardButton("🙈 Більше не показувати", callback_data=f"{CB_HIDE}{short_link}")],
    ])

def build_restore_keyboard(short_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Відновити вакансію", callback_data=f"{CB_RESTORE}{short_link}")],
    ])


# ── Фільтр по даті ────────────────────────────────────────────────────────────

def filter_by_days(sources: list[MergedSource], days: int) -> list[MergedSource]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for ms in sources:
        kept = [
            v for v in ms.vacancies
            if v.pub_dt is None or _safe_gte(v.pub_dt, cutoff)
        ]
        result.append(MergedSource(
            name=ms.name, vacancies=kept,
            total_before=ms.total_before, duplicates=ms.duplicates,
        ))
    return result

def _safe_gte(dt: datetime, cutoff: datetime) -> bool:
    try:
        return dt >= cutoff
    except TypeError:
        return True


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

def format_vacancy(vacancy: Vacancy, num: int = 0) -> str:
    lines = []
    if num:
        lines += [f"📌 <b>Вакансія #{num}</b>", ""]
    lines += [f"💼 <b>Спеціальність:</b> {html.escape(vacancy.title)}", ""]
    if vacancy.company:
        lines += [f"🏢 <b>Компанія:</b> {html.escape(vacancy.company)}", ""]
    if vacancy.location:
        lines += [f"📍 <b>Місце роботи:</b> {html.escape(vacancy.location)}", ""]
    lines += [f"💰 <b>Зарплата:</b> {html.escape(clean_salary(vacancy.salary))}", ""]
    lines += [f"📅 <b>Дата публікації:</b> {html.escape(vacancy.published)}", ""]
    lines.append(f"🌐 <b>Сайт:</b> {html.escape(vacancy.source)}")
    return "\n".join(lines)

def build_summary(sources: list[MergedSource], days: int) -> str:
    total = sum(len(ms.vacancies) for ms in sources)
    lines = [f"🗂 <b>За останні {days} д. знайдено {total} вакансій:</b>\n"]
    for ms in sources:
        dup = f" (видалено {ms.duplicates} дублікатів)" if ms.duplicates else ""
        lines.append(f"  • {ms.name} — {len(ms.vacancies)} вак.{dup}")
    return "\n".join(lines)


# ── Надсилання вакансій ───────────────────────────────────────────────────────

async def _send_vacancies(chat, pending: list[dict]) -> list[int]:
    """Надсилає список вакансій окремими повідомленнями. Повертає message_ids."""
    ids = []
    for i, vd in enumerate(pending, 1):
        v = Vacancy(**vd)
        image = generate_vacancy_image(v.title, i)
        msg = await chat.send_photo(
            photo=image,
            caption=format_vacancy(v, i),
            parse_mode=ParseMode.HTML,
            reply_markup=build_vacancy_keyboard(v),
        )
        ids.append(msg.message_id)
    return ids


# ── Обробники: /start та Reply Keyboard ───────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Зберігаємо ID команди /start щоб не видаляти її при очищенні
    context.chat_data["start_message_id"] = update.message.message_id
    await update.message.reply_text(
        "Натисни кнопку меню внизу щоб обрати дію.",
        reply_markup=build_persistent_keyboard(),
    )


async def track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        track(context, update.message.message_id)


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    track(context, update.message.message_id)
    if text == BTN_VAC_1D:
        await _request_vacancies(update.message, context, days=1)
    elif text == BTN_VAC_14D:
        await _request_vacancies(update.message, context, days=14)
    elif text == BTN_SHOW_HIDDEN:
        await _show_hidden(update.message.chat, context, update)
    elif text == BTN_CLEAR:
        await _do_clear_history(update.message.chat_id, update.message.message_id, context)
    elif text == BTN_CLEAR_HIDE:
        await _do_clear_hidden(update.message.chat, context, update)


# ── Логіка перегляду вакансій ─────────────────────────────────────────────────

async def _request_vacancies(message, context: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    """Завантажує вакансії, показує зведення + питання Так/Ні."""
    msg = await message.reply_text("⏳ Завантажую вакансії, зачекай...")
    track(context, msg.message_id)

    all_sources = fetch_all_vacancies()
    filtered = filter_by_days(all_sources, days)
    hidden = context.bot_data.get(f"hidden_{message.chat.id}", {}) if hasattr(message, 'chat') else {}

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)

    context.chat_data["pending_vacancies"] = [
        {"source": v.source, "title": v.title, "link": v.link,
         "published": v.published, "pub_dt": v.pub_dt,
         "company": v.company, "location": v.location, "salary": v.salary}
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    summary = build_summary(filtered, days)

    if total == 0:
        await msg.edit_text(
            summary + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
        )
        return

    confirm_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days}"),
        InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
    ]])
    await msg.edit_text(
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

    all_sources = fetch_all_vacancies()
    filtered = filter_by_days(all_sources, days)
    hidden = get_hidden(context, update)

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)
    summary = build_summary(filtered, days)

    context.chat_data["pending_vacancies"] = [
        {"source": v.source, "title": v.title, "link": v.link,
         "published": v.published, "pub_dt": v.pub_dt,
         "company": v.company, "location": v.location, "salary": v.salary}
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    if total == 0:
        await query.edit_message_text(
            summary + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
        )
        return

    confirm_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days}"),
        InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
    ]])
    await query.edit_message_text(
        summary + "\n\n❓ <b>Показати вакансії?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_kb,
    )


async def vacancies_1d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=1)

async def vacancies_14d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=14)


async def confirm_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    pending = context.chat_data.pop("pending_vacancies", [])
    if not pending:
        msg = await query.message.reply_text("Вакансії недоступні — оновіть список.")
        track(context, msg.message_id)
        return

    ids = await _send_vacancies(query.message.chat, pending)
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

async def _do_clear_history(chat_id: int, current_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    track(context, current_id)
    saved_ids = context.chat_data.get("message_ids", [])
    min_id = min(saved_ids) if saved_ids else current_id
    start_id = context.chat_data.get("start_message_id")
    context.chat_data["message_ids"] = []

    deleted = 0
    for mid in range(min_id, current_id + 1):
        if mid == start_id:
            continue  # не видаляємо повідомлення /start
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🗑 Листування очищено.\n\nНатисни меню щоб обрати дію 👇",
    )
    track(context, msg.message_id)


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🗑 Очищую...")
    await _do_clear_history(query.message.chat_id, query.message.message_id, context)


async def _do_clear_hidden(chat, context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    hidden = get_hidden(context, update)
    count = len(hidden)
    set_hidden(context, update, {})
    msg = await chat.send_message(f"✅ Список HIDDEN очищено ({count} вакансій відновлено).")
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
    app.add_handler(CallbackQueryHandler(vacancies_1d,    pattern=f"^{CB_VAC_1D}$"))
    app.add_handler(CallbackQueryHandler(vacancies_14d,   pattern=f"^{CB_VAC_14D}$"))
    app.add_handler(CallbackQueryHandler(confirm_show,    pattern=f"^{CB_CONFIRM_YES}"))
    app.add_handler(CallbackQueryHandler(confirm_no,      pattern=f"^{CB_CONFIRM_NO}$"))
    app.add_handler(CallbackQueryHandler(show_hidden_list,pattern=f"^{CB_SHOW_HIDDEN}$"))
    app.add_handler(CallbackQueryHandler(clear_history,   pattern=f"^{CB_CLEAR}$"))
    app.add_handler(CallbackQueryHandler(clear_hidden,    pattern=f"^{CB_CLEAR_HIDE}$"))
    app.add_handler(CallbackQueryHandler(hide_vacancy,    pattern=f"^{CB_HIDE}"))
    app.add_handler(CallbackQueryHandler(unhide_vacancy,  pattern=f"^{CB_UNHIDE}"))
    app.add_handler(CallbackQueryHandler(restore_vacancy, pattern=f"^{CB_RESTORE}"))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(f"^({'|'.join(re.escape(b) for b in ALL_BTN_TEXTS)})$"),
        handle_reply_button,
    ))
    app.add_handler(MessageHandler(filters.ALL, track_user_message))

    logger.info("Бот запущений. Ctrl+C для зупинки.")
    app.run_polling()


if __name__ == "__main__":
    main()