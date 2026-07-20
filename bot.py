"""
Telegram-бот з меню 3 кнопок:
  - Вакансії за 1 день
  - Вакансії за 14 днів
  - Очистити листування
"""

import html
import logging
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from feeds import fetch_all_vacancies, Vacancy
from image_gen import generate_vacancy_image

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

CB_VACANCIES_1D  = "vacancies_1d"
CB_VACANCIES_14D = "vacancies_14d"
CB_CLEAR         = "clear_history"

MAX_PER_SOURCE = 20


# ── Утиліта трекінгу ID ───────────────────────────────────────────────────────

def track(context: ContextTypes.DEFAULT_TYPE, *message_ids: int) -> None:
    """Зберігає ID повідомлень для подальшого видалення."""
    context.chat_data.setdefault("message_ids", []).extend(message_ids)


# ── Клавіатура ────────────────────────────────────────────────────────────────

def build_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Вакансії за 1 день",  callback_data=CB_VACANCIES_1D)],
        [InlineKeyboardButton("📆 Вакансії за 14 днів", callback_data=CB_VACANCIES_14D)],
        [InlineKeyboardButton("🗑 Очистити листування",  callback_data=CB_CLEAR)],
    ])


# ── Фільтр по даті ────────────────────────────────────────────────────────────

def filter_by_days(results: dict, days: int) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = {}
    for source, vacancies in results.items():
        kept = []
        for v in vacancies:
            if v.pub_dt is None:
                kept.append(v)
                continue
            try:
                if v.pub_dt >= cutoff:
                    kept.append(v)
            except TypeError:
                kept.append(v)
        filtered[source] = kept
    return filtered


# ── Форматування ──────────────────────────────────────────────────────────────

def format_vacancy(vacancy: Vacancy, num: int = 0) -> str:
    title     = html.escape(vacancy.title)
    company   = html.escape(vacancy.company)
    location  = html.escape(vacancy.location)
    published = html.escape(vacancy.published)
    source    = html.escape(vacancy.source)
    salary    = html.escape(vacancy.salary) if vacancy.salary else "не вказано"

    lines = []
    if num:
        lines.append(f"📌 <b>Вакансія #{num}</b>")
        lines.append("")
    lines.append(f"💼 <b>Спеціальність:</b> {title}")
    lines.append("")
    if company:
        lines.append(f"🏢 <b>Компанія:</b> {company}")
        lines.append("")
    if location:
        lines.append(f"📍 <b>Місце роботи:</b> {location}")
        lines.append("")
    lines.append(f"💰 <b>Зарплата:</b> {salary}")
    lines.append("")
    lines.append(f"📅 <b>Дата публікації:</b> {published}")
    lines.append("")
    lines.append(f"🌐 <b>Сайт:</b> {source}")
    return "\n".join(lines)


def build_vacancy_keyboard(link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Відкрити вакансію", url=link)]
    ])


def build_summary(results: dict, days: int) -> str:
    total = sum(len(v) for v in results.values())
    lines = [f"🗂 <b>TEST MODE — зведення (без фільтру дат):</b>\n"]
    for source, vacancies in results.items():
        lines.append(f"  • {source} — {len(vacancies)} вак.")
    lines.append(f"\n<b>Всього: {total} вакансій</b>")
    return "\n".join(lines)


# ── Обробники ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track(context, update.message.message_id)
    msg = await update.message.reply_text(
        "Привіт! Обери дію 👇",
        reply_markup=build_main_keyboard(),
    )
    track(context, msg.message_id)


async def track_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Трекує всі вхідні повідомлення від користувача."""
    if update.message:
        track(context, update.message.message_id)


async def show_vacancies(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    query = update.callback_query
    await query.answer()
    track(context, query.message.message_id)
    await query.edit_message_text("⏳ Завантажую вакансії, зачекай...")

    all_results = fetch_all_vacancies()
    total = sum(len(v) for v in all_results.values())

    if total == 0:
        msg = await query.message.reply_text(
            "⚠️ Жодної вакансії не знайдено. Перевір логи в консолі.",
            reply_markup=build_main_keyboard(),
        )
        track(context, msg.message_id)
        return

    msg = await query.message.reply_text(
        build_summary(all_results, days),
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_keyboard(),
    )
    track(context, msg.message_id)


async def vacancies_1d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=1)


async def vacancies_14d(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vacancies(update, context, days=14)


async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видаляє всі повідомлення які бот накопичив з початку сесії."""
    query = update.callback_query
    await query.answer("🗑 Очищую листування...")

    chat_id = query.message.chat_id

    # Додаємо поточне повідомлення з кнопкою
    track(context, query.message.message_id)

    all_ids: list[int] = list(set(context.chat_data.get("message_ids", [])))
    context.chat_data["message_ids"] = []

    deleted = 0
    for mid in all_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
        except Exception:
            pass  # вже видалено або недоступне (>48г) — пропускаємо

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Листування очищено. Обери дію 👇",
        reply_markup=build_main_keyboard(),
    )
    track(context, msg.message_id)


# ── Запуск ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не знайдено. Створи .env з BOT_TOKEN=...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(vacancies_1d,  pattern=f"^{CB_VACANCIES_1D}$"))
    app.add_handler(CallbackQueryHandler(vacancies_14d, pattern=f"^{CB_VACANCIES_14D}$"))
    app.add_handler(CallbackQueryHandler(clear_history, pattern=f"^{CB_CLEAR}$"))
    # Трекуємо всі вхідні повідомлення від користувача (команди, текст тощо)
    app.add_handler(MessageHandler(filters.ALL, track_user_message))

    logger.info("Бот запущений. Ctrl+C для зупинки.")
    app.run_polling()


if __name__ == "__main__":
    main()