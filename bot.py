"""
Telegram-бот: перегляд вакансій, hide/unhide per-user, очищення листування.
HIDDEN_VACANCIES зберігається в bot_data[user_id] — окремо для кожного юзера.
"""

import html
import logging
import os
import re
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
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

# ── Callback префікси ─────────────────────────────────────────────────────────
CB_VAC_1D        = "vacancies_1d"
CB_VAC_14D       = "vacancies_14d"
CB_CLEAR         = "clear_history"
CB_CLEAR_HIDE    = "clear_hidden"
CB_SHOW_HIDDEN   = "show_hidden"
CB_MENU          = "open_menu"
CB_CONFIRM_YES   = "confirm_yes:"  # confirm_yes:<days>
CB_CONFIRM_NO    = "confirm_no"
CB_HIDE          = "hide:"
CB_UNHIDE        = "unhide:"
CB_RESTORE       = "restore:"

MAX_PER_SOURCE = 20


# ── Per-user сховище HIDDEN_VACANCIES ────────────────────────────────────────

def _user_key(update_or_query) -> str:
    """Повертає унікальний ключ юзера для bot_data."""
    if hasattr(update_or_query, "from_user"):
        uid = update_or_query.from_user.id
    elif hasattr(update_or_query, "effective_user"):
        uid = update_or_query.effective_user.id
    else:
        uid = 0
    return f"hidden_{uid}"


def get_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update) -> dict[str, str]:
    key = _user_key(update.effective_user)
    return context.bot_data.setdefault(key, {})


def set_hidden(context: ContextTypes.DEFAULT_TYPE, update: Update, data: dict) -> None:
    key = _user_key(update.effective_user)
    context.bot_data[key] = data


# ── Трекінг повідомлень для очищення ─────────────────────────────────────────

def track(context: ContextTypes.DEFAULT_TYPE, *ids: int) -> None:
    context.chat_data.setdefault("message_ids", []).extend(ids)


# ── Клавіатури ────────────────────────────────────────────────────────────────

def build_menu_button() -> InlineKeyboardMarkup:
    """Одна кнопка '☰ Меню' — показується під повідомленнями бота."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☰ Меню", callback_data=CB_MENU)],
    ])


def build_menu_keyboard() -> InlineKeyboardMarkup:
    """Розгорнуте меню з усіма діями."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Вакансії за 1 день",      callback_data=CB_VAC_1D)],
        [InlineKeyboardButton("📆 Вакансії за 14 днів",     callback_data=CB_VAC_14D)],
        [InlineKeyboardButton("👁 Відновлені вакансії",      callback_data=CB_SHOW_HIDDEN)],
        [InlineKeyboardButton("🗑 Очистити листування",      callback_data=CB_CLEAR)],
        [InlineKeyboardButton("🚫 Видалити список HIDDEN",   callback_data=CB_CLEAR_HIDE)],
    ])


# ── Фільтр по даті ────────────────────────────────────────────────────────────

def filter_by_days(sources: list[MergedSource], days: int) -> list[MergedSource]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for ms in sources:
        kept = []
        for v in ms.vacancies:
            if v.pub_dt is None:
                kept.append(v)
                continue
            try:
                if v.pub_dt >= cutoff:
                    kept.append(v)
            except TypeError:
                kept.append(v)
        result.append(MergedSource(
            name=ms.name,
            vacancies=kept,
            total_before=ms.total_before,
            duplicates=ms.duplicates,
        ))
    return result


# ── Форматування ──────────────────────────────────────────────────────────────

# Текст кнопки постійного меню (Reply Keyboard)
MENU_BUTTON_TEXT = "☰ Меню"  # залишаємо для сумісності з обробником

# Тексти кнопок постійної панелі
BTN_VAC_1D       = "📅 Вакансії за 1 день"
BTN_VAC_14D      = "📆 Вакансії за 14 днів"
BTN_SHOW_HIDDEN  = "👁 Відновлені вакансії"
BTN_CLEAR        = "🗑 Очистити листування"
BTN_CLEAR_HIDE   = "🚫 Видалити HIDDEN"


def build_persistent_keyboard() -> ReplyKeyboardMarkup:
    """Постійна панель внизу з усіма діями — завжди доступна."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_VAC_1D), KeyboardButton(BTN_VAC_14D)],
            [KeyboardButton(BTN_SHOW_HIDDEN)],
            [KeyboardButton(BTN_CLEAR), KeyboardButton(BTN_CLEAR_HIDE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def clean_salary(raw: str) -> str:
    """
    Очищує рядок зарплати:
    - прибирає HTML-entities (&nbsp; тощо)
    - прибирає 'від' якщо немає діапазону (лише одна цифра)
    - залишає тільки числа і валютний символ
    """
    if not raw:
        return "не вказано"

    # Декодуємо HTML-entities (&nbsp; → пробіл, &amp; → & тощо)
    text = html.unescape(raw)

    # Прибираємо зайві пробіли (зокрема non-breaking space \xa0)
    text = re.sub(r'[\s\xa0]+', ' ', text).strip()

    # Перевіряємо чи є діапазон (два числа через тире)
    has_range = bool(re.search(r'\d[\s]*[–\-][\s]*\d', text))

    # Якщо є "від" але немає діапазону — прибираємо "від"
    if not has_range:
        text = re.sub(r'\bвід\b\s*', '', text, flags=re.IGNORECASE).strip()

    return text or "не вказано"


def format_vacancy(vacancy: Vacancy, num: int = 0) -> str:
    salary_text = clean_salary(vacancy.salary)
    lines = []
    if num:
        lines += [f"📌 <b>Вакансія #{num}</b>", ""]
    lines += [f"💼 <b>Спеціальність:</b> {html.escape(vacancy.title)}", ""]
    if vacancy.company:
        lines += [f"🏢 <b>Компанія:</b> {html.escape(vacancy.company)}", ""]
    if vacancy.location:
        lines += [f"📍 <b>Місце роботи:</b> {html.escape(vacancy.location)}", ""]
    lines += [f"💰 <b>Зарплата:</b> {html.escape(salary_text)}", ""]
    lines += [f"📅 <b>Дата публікації:</b> {html.escape(vacancy.published)}", ""]
    lines.append(f"🌐 <b>Сайт:</b> {html.escape(vacancy.source)}")
    return "\n".join(lines)


def build_vacancy_keyboard(vacancy: Vacancy) -> InlineKeyboardMarkup:
    short_link = vacancy.link[:50]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Відкрити вакансію", url=vacancy.link)],
        [InlineKeyboardButton("🙈 Більше не показувати", callback_data=f"{CB_HIDE}{short_link}")],
    ])


def build_restore_keyboard(short_link: str) -> InlineKeyboardMarkup:
    """Кнопка 'Відновити вакансію' — видаляє зі списку HIDDEN, візуально нічого."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♻️ Відновити вакансію", callback_data=f"{CB_RESTORE}{short_link}")],
    ])


def build_summary(sources: list[MergedSource], days: int) -> str:
    total = sum(len(ms.vacancies) for ms in sources)
    lines = [f"🗂 <b>За останні {days} д. знайдено {total} вакансій:</b>\n"]
    for ms in sources:
        dup_note = f" (видалено {ms.duplicates} дублікатів)" if ms.duplicates else ""
        lines.append(f"  • {ms.name} — {len(ms.vacancies)} вак.{dup_note}")
    return "\n".join(lines)


# ── Обробники ─────────────────────────────────────────────────────────────────

async def open_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Відкриває меню з усіма діями як нове повідомлення."""
    query = update.callback_query
    await query.answer()
    msg = await query.message.reply_text(
        "Обери дію 👇",
        reply_markup=build_menu_keyboard(),
    )
    track(context, msg.message_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track(context, update.message.message_id)
    msg = await update.message.reply_text(
        "Привіт! Натисни '☰ Меню' внизу щоб обрати дію.",
        reply_markup=build_persistent_keyboard(),
    )
    track(context, msg.message_id)


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє натискання будь-якої кнопки з постійної Reply Keyboard панелі."""
    text = update.message.text
    track(context, update.message.message_id)

    if text == BTN_VAC_1D:
        await _show_vacancies_from_message(update, context, days=1)
    elif text == BTN_VAC_14D:
        await _show_vacancies_from_message(update, context, days=14)
    elif text == BTN_SHOW_HIDDEN:
        await _show_hidden_from_message(update, context)
    elif text == BTN_CLEAR:
        await _clear_history_from_message(update, context)
    elif text == BTN_CLEAR_HIDE:
        await _clear_hidden_from_message(update, context)


async def _show_vacancies_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    """Аналог show_vacancies але викликається з Reply Keyboard (не callback)."""
    msg = await update.message.reply_text("⏳ Завантажую вакансії, зачекай...")
    track(context, msg.message_id)

    all_sources = fetch_all_vacancies()
    filtered    = filter_by_days(all_sources, days)
    hidden      = get_hidden(context, update)

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)
    summary_text = build_summary(filtered, days)

    context.chat_data["pending_vacancies"] = [
        {
            "source": v.source, "title": v.title, "link": v.link,
            "published": v.published, "pub_dt": v.pub_dt,
            "company": v.company, "location": v.location, "salary": v.salary,
        }
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    if total == 0:
        await msg.edit_text(
            summary_text + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
        )
        return

    confirm_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days}"),
            InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
        ]
    ])
    await msg.edit_text(
        summary_text + "\n\n❓ <b>Показати вакансії?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard,
    )
    track(context, msg.message_id)


async def _show_hidden_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hidden = get_hidden(context, update)
    if not hidden:
        msg = await update.message.reply_text("📭 Список прихованих вакансій порожній.")
        track(context, msg.message_id)
        return
    msg = await update.message.reply_text(
        f"🙈 <b>Приховані вакансії ({len(hidden)}):</b>\n\nНатисни 'Відновити' під потрібною.",
        parse_mode=ParseMode.HTML,
    )
    track(context, msg.message_id)
    for short_link, title in hidden.items():
        msg = await update.message.chat.send_message(
            f"🙈 <b>{html.escape(title)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_restore_keyboard(short_link),
        )
        track(context, msg.message_id)


async def _clear_history_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    current_id = update.message.message_id
    track(context, current_id)
    saved_ids = context.chat_data.get("message_ids", [])
    context.chat_data["message_ids"] = []
    min_id = min(saved_ids) if saved_ids else current_id
    deleted = 0
    for mid in range(min_id, current_id + 1):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Видалено {deleted} повідомлень.",
    )
    track(context, msg.message_id)


async def _clear_hidden_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hidden = get_hidden(context, update)
    count = len(hidden)
    set_hidden(context, update, {})
    msg = await update.message.reply_text(
        f"✅ Список HIDDEN очищено ({count} вакансій відновлено).",
    )
    track(context, msg.message_id)


async def track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        track(context, update.message.message_id)


async def show_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    """Крок 1: завантажує вакансії, показує зведення і питання 'Показати вакансії?'"""
    query = update.callback_query
    await query.answer()
    track(context, query.message.message_id)
    await query.edit_message_text("⏳ Завантажую вакансії, зачекай...")

    all_sources = fetch_all_vacancies()
    filtered    = filter_by_days(all_sources, days)
    hidden      = get_hidden(context, update)

    for ms in filtered:
        ms.vacancies = [v for v in ms.vacancies if v.link[:50] not in hidden]

    total = sum(len(ms.vacancies) for ms in filtered)
    summary_text = build_summary(filtered, days)

    # Зберігаємо відфільтровані вакансії в chat_data для кроку 2
    context.chat_data["pending_vacancies"] = [
        {
            "source": v.source, "title": v.title, "link": v.link,
            "published": v.published, "pub_dt": v.pub_dt,
            "company": v.company, "location": v.location, "salary": v.salary,
        }
        for ms in filtered for v in ms.vacancies[:MAX_PER_SOURCE]
    ]

    if total == 0:
        await query.edit_message_text(
            summary_text + "\n\nВакансій не знайдено (або всі приховані).",
            parse_mode=ParseMode.HTML,
            reply_markup=build_menu_button(),
        )
        return

    # Показуємо зведення + питання з кнопками Так/Ні
    confirm_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Так", callback_data=f"{CB_CONFIRM_YES}{days}"),
            InlineKeyboardButton("❌ Ні",  callback_data=CB_CONFIRM_NO),
        ]
    ])
    await query.edit_message_text(
        summary_text + "\n\n❓ <b>Показати вакансії?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard,
    )


async def confirm_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Крок 2: виводить вакансії якщо користувач натиснув 'Так'."""
    query = update.callback_query
    await query.answer()

    days = int(query.data[len(CB_CONFIRM_YES):])
    pending = context.chat_data.pop("pending_vacancies", [])

    # Прибираємо кнопки з повідомлення зведення
    await query.edit_message_reply_markup(reply_markup=None)

    if not pending:
        msg = await query.message.reply_text(
            "Вакансії недоступні — спробуй оновити список.",
            reply_markup=build_menu_button(),
        )
        track(context, msg.message_id)
        return

    counter = 1
    for vd in pending:
        v = Vacancy(**vd)
        image = generate_vacancy_image(v.title, counter)
        msg = await query.message.chat.send_photo(
            photo=image,
            caption=format_vacancy(v, counter),
            parse_mode=ParseMode.HTML,
            reply_markup=build_vacancy_keyboard(v),
        )
        track(context, msg.message_id)
        counter += 1

    msg = await query.message.chat.send_message(
        "Обери наступну дію 👇",
        reply_markup=build_menu_button(),
    )
    track(context, msg.message_id)


async def confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Крок 2: користувач натиснув 'Ні' — прибираємо кнопки і надсилаємо відповідь."""
    query = update.callback_query
    await query.answer()

    context.chat_data.pop("pending_vacancies", None)

    await query.edit_message_reply_markup(reply_markup=None)
    msg = await query.message.reply_text(
        "👍 Добре, почекаємо оновлень!",
        reply_markup=build_menu_button(),
    )
    track(context, msg.message_id)


async def vacancies_1d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=1)


async def vacancies_14d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=14)


async def hide_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Додає вакансію до HIDDEN юзера, замінює повідомлення на заглушку."""
    query = update.callback_query
    await query.answer()

    short_link = query.data[len(CB_HIDE):]
    hidden = get_hidden(context, update)

    # Витягуємо назву з caption
    caption = query.message.caption or ""
    title = "вакансія"
    for line in caption.split("\n"):
        if "Спеціальність:" in line:
            title = line.replace("💼", "").replace("Спеціальність:", "").strip()
            break

    hidden[short_link] = title
    set_hidden(context, update, hidden)

    await query.edit_message_caption(
        caption=f"🙈 <b>Приховано:</b> {html.escape(title)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"👁 Відновити до перегляду",
                callback_data=f"{CB_UNHIDE}{short_link}",
            )]
        ]),
    )


async def unhide_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Відновлює вакансію: редагує повідомлення 'Приховано' на повноцінний вигляд."""
    query = update.callback_query
    await query.answer()

    short_link = query.data[len(CB_UNHIDE):]
    hidden = get_hidden(context, update)
    title = hidden.pop(short_link, "вакансія")
    set_hidden(context, update, hidden)

    # Шукаємо вакансію у свіжих даних
    all_sources = fetch_all_vacancies()
    found: Vacancy | None = None
    for ms in all_sources:
        for v in ms.vacancies:
            if v.link[:50] == short_link:
                found = v
                break
        if found:
            break

    if found:
        # Редагуємо існуюче повідомлення: нова картинка + повний текст + кнопки
        from telegram import InputMediaPhoto
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
        # Якщо вакансія не знайдена — редагуємо caption з повідомленням
        await query.edit_message_caption(
            caption=f"👁 <b>{html.escape(title)}</b>\n\nВакансія відновлена. Оновіть список для повних деталей.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_menu_button(),
        )


async def show_hidden_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує список всіх прихованих вакансій юзера з кнопкою 'Відновити'."""
    query = update.callback_query
    await query.answer()

    hidden = get_hidden(context, update)

    if not hidden:
        msg = await query.message.reply_text(
            "📭 Список прихованих вакансій порожній.",
            reply_markup=build_menu_button(),
        )
        track(context, msg.message_id)
        return

    msg = await query.message.reply_text(
        f"🙈 <b>Приховані вакансії ({len(hidden)}):</b>\n\nНатисни 'Відновити' під потрібною вакансією.",
        parse_mode=ParseMode.HTML,
    )
    track(context, msg.message_id)

    for short_link, title in hidden.items():
        msg = await query.message.chat.send_message(
            f"🙈 <b>{html.escape(title)}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_restore_keyboard(short_link),
        )
        track(context, msg.message_id)

    msg = await query.message.chat.send_message(
        "Обери дію 👇",
        reply_markup=build_menu_button(),
    )
    track(context, msg.message_id)


async def restore_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видаляє вакансію зі списку HIDDEN. Візуально нічого не змінюється."""
    query = update.callback_query
    await query.answer("✅ Вакансію відновлено")

    short_link = query.data[len(CB_RESTORE):]
    hidden = get_hidden(context, update)
    hidden.pop(short_link, None)
    set_hidden(context, update, hidden)

    # Прибираємо кнопку "Відновити" — залишаємо тільки текст назви
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def clear_hidden(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очищує весь список HIDDEN для юзера."""
    query = update.callback_query
    await query.answer()
    hidden = get_hidden(context, update)
    count = len(hidden)
    set_hidden(context, update, {})
    msg = await query.message.reply_text(
        f"✅ Список HIDDEN очищено ({count} вакансій відновлено).",
        reply_markup=build_menu_button(),
    )
    track(context, msg.message_id)


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🗑 Очищую...")
    chat_id = query.message.chat_id
    current_id = query.message.message_id

    # Додаємо поточне повідомлення в трекер
    track(context, current_id)

    saved_ids = context.chat_data.get("message_ids", [])
    context.chat_data["message_ids"] = []

    # Перебираємо діапазон від найпершого збереженого ID до поточного включно.
    # Це гарантує що всі повідомлення в чаті будуть видалені — навіть ті
    # що не потрапили в track() (фото, системні, редаговані тощо).
    min_id = min(saved_ids) if saved_ids else current_id
    all_ids = range(min_id, current_id + 1)

    deleted = 0
    for mid in all_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass  # вже видалено або недоступне (>48г) — пропускаємо

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ Видалено {deleted} повідомлень. Обери дію 👇",
        reply_markup=build_menu_button(),
    )
    track(context, msg.message_id)


# ── Запуск ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не знайдено. Створи .env з BOT_TOKEN=...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(open_menu,       pattern=f"^{CB_MENU}$"))
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
    # Постійна Reply Keyboard — всі кнопки через один обробник
    reply_buttons_pattern = "|".join(re.escape(b) for b in [
        BTN_VAC_1D, BTN_VAC_14D, BTN_SHOW_HIDDEN, BTN_CLEAR, BTN_CLEAR_HIDE, MENU_BUTTON_TEXT,
    ])
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(f"^({reply_buttons_pattern})$"),
        handle_reply_button,
    ))
    app.add_handler(MessageHandler(filters.ALL, track_user_message))

    logger.info("Бот запущений. Ctrl+C для зупинки.")
    app.run_polling()


if __name__ == "__main__":
    main()