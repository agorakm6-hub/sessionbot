"""
Telegram-бот для приёма жалоб на нарушения (каналы/чаты/боты).

Логика:
1. /start -> выбор языка (RU/UA/EN)
2. Подтверждение "я не робот"
3. Главное меню -> кнопка "Сообщить о нарушении"
4. Пошагово: ссылка/юзернейм -> причина -> доказательство (текст/скриншот)
5. Итоговая сводка с кнопками "Подтвердить" / "Изменить"
6. При подтверждении жалоба отправляется в чат модераторов
7. Модераторы видят кнопки: "✅ Принять жалобу" и "❌ Отклонить жалобу"
8. При принятии/отклонении пользователю приходит уведомление
9. /reports в чате модераторов - список всех жалоб
10. Веб-сервер на порту 10000 для успешного деплоя на Render
"""

import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiohttp import web

# ============ НАСТРОЙКИ ============

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ТОКЕН_БОТА")
MOD_CHAT_ID = -1004354663980  # ID чата модераторов
REPORTS_FILE = "reports.json"  # Файл для хранения жалоб

# ============ ТЕКСТЫ ПО ЯЗЫКАМ ============

TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык:",
        "confirm_bot": "Подтвердите, что вы не робот:",
        "confirm_bot_btn": "Я не робот 🤖",
        "main_menu": "Главное меню",
        "report_btn": "🚨 Сообщить о нарушении",
        "enter_link": "Введите ссылку на канал/чат или юзернейм бота-нарушителя:",
        "enter_reason": "Введите причину нарушения:",
        "enter_proof": "Отправьте ссылку или скриншот нарушения:",
        "summary": "Проверьте вашу жалобу:\n\n🔗 Канал/бот: {link}\n📝 Причина: {reason}\n📎 Доказательство: {proof}",
        "confirm_btn": "✅ Подтвердить",
        "edit_btn": "✏️ Изменить",
        "edit_choose": "Что вы хотите изменить?",
        "edit_link_btn": "Ссылку/юзернейм",
        "edit_reason_btn": "Причину",
        "edit_proof_btn": "Доказательство",
        "back_btn": "⬅️ Назад",
        "sent": "✅ Ваша жалоба отправлена модераторам и будет рассмотрена в ближайшее время. Спасибо!",
        "photo_placeholder": "[скриншот]",
        "no_reports": "📭 Жалоб пока нет",
        "reports_title": "📋 Список жалоб (последние 7 дней):\n\n",
        "report_item": "👤 От: {user}\n🔗 Нарушитель: {link}\n📝 Причина: {reason}\n📎 Доказательство: {proof}\n📅 Дата: {date}\n\n",
        "moderator_only": "❌ Эта команда только для модераторов",
        "report_accepted": "✅ Модераторы приняли вашу жалобу! Ожидайте решения.",
        "report_rejected": "❌ Ваша жалоба была отклонена модераторами.",
    },
    "ua": {
        "choose_lang": "Оберіть мову:",
        "confirm_bot": "Підтвердіть, що ви не робот:",
        "confirm_bot_btn": "Я не робот 🤖",
        "main_menu": "Головне меню",
        "report_btn": "🚨 Повідомити про порушення",
        "enter_link": "Введіть посилання на канал/чат або юзернейм бота-порушника:",
        "enter_reason": "Введіть причину порушення:",
        "enter_proof": "Надішліть посилання або скріншот порушення:",
        "summary": "Перевірте вашу скаргу:\n\n🔗 Канал/бот: {link}\n📝 Причина: {reason}\n📎 Доказ: {proof}",
        "confirm_btn": "✅ Підтвердити",
        "edit_btn": "✏️ Змінити",
        "edit_choose": "Що ви хочете змінити?",
        "edit_link_btn": "Посилання/юзернейм",
        "edit_reason_btn": "Причину",
        "edit_proof_btn": "Доказ",
        "back_btn": "⬅️ Назад",
        "sent": "✅ Вашу скаргу надіслано модераторам, її розглянуть найближчим часом. Дякуємо!",
        "photo_placeholder": "[скріншот]",
        "no_reports": "📭 Скарг поки немає",
        "reports_title": "📋 Список скарг (останні 7 днів):\n\n",
        "report_item": "👤 Від: {user}\n🔗 Порушник: {link}\n📝 Причина: {reason}\n📎 Доказ: {proof}\n📅 Дата: {date}\n\n",
        "moderator_only": "❌ Ця команда тільки для модераторів",
        "report_accepted": "✅ Модератори прийняли вашу скаргу! Очікуйте рішення.",
        "report_rejected": "❌ Вашу скаргу відхилено модераторами.",
    },
    "en": {
        "choose_lang": "Choose language:",
        "confirm_bot": "Please confirm you're not a robot:",
        "confirm_bot_btn": "I'm not a robot 🤖",
        "main_menu": "Main menu",
        "report_btn": "🚨 Report a violation",
        "enter_link": "Enter the channel/chat link or the violating bot's username:",
        "enter_reason": "Enter the reason for the report:",
        "enter_proof": "Send a link or a screenshot as proof:",
        "summary": "Please check your report:\n\n🔗 Channel/bot: {link}\n📝 Reason: {reason}\n📎 Proof: {proof}",
        "confirm_btn": "✅ Confirm",
        "edit_btn": "✏️ Edit",
        "edit_choose": "What do you want to edit?",
        "edit_link_btn": "Link/username",
        "edit_reason_btn": "Reason",
        "edit_proof_btn": "Proof",
        "back_btn": "⬅️ Back",
        "sent": "✅ Your report has been sent to the moderators and will be reviewed soon. Thank you!",
        "photo_placeholder": "[screenshot]",
        "no_reports": "📭 No reports yet",
        "reports_title": "📋 List of reports (last 7 days):\n\n",
        "report_item": "👤 From: {user}\n🔗 Violator: {link}\n📝 Reason: {reason}\n📎 Proof: {proof}\n📅 Date: {date}\n\n",
        "moderator_only": "❌ This command is for moderators only",
        "report_accepted": "✅ Moderators have accepted your report! Please wait for a decision.",
        "report_rejected": "❌ Your report has been rejected by moderators.",
    },
}

# ============ ХРАНЕНИЕ ЖАЛОБ ============

def load_reports() -> List[Dict]:
    """Загружает жалобы из файла"""
    if not os.path.exists(REPORTS_FILE):
        return []
    try:
        with open(REPORTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_report(report: Dict):
    """Сохраняет новую жалобу"""
    reports = load_reports()
    reports.append(report)
    # Оставляем только жалобы за последние 30 дней
    cutoff = datetime.now() - timedelta(days=30)
    reports = [r for r in reports if datetime.fromisoformat(r['date']) > cutoff]
    with open(REPORTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

def update_report_status(user_id: int, status: str):
    """Обновляет статус жалобы пользователя"""
    reports = load_reports()
    for report in reports:
        if report['user_id'] == user_id:
            report['status'] = status
            break
    with open(REPORTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)

# ============ СОСТОЯНИЯ (FSM) ============

class ReportForm(StatesGroup):
    link = State()
    reason = State()
    proof = State()
    confirm = State()
    edit_choice = State()

router = Router()

# ============ КЛАВИАТУРЫ ============

def kb_language() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_ua")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
        ]
    )

def kb_confirm_human(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["confirm_bot_btn"], callback_data="confirm_human")]
        ]
    )

def kb_main_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["report_btn"], callback_data="report_start")]
        ]
    )

def kb_summary(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["confirm_btn"], callback_data="report_confirm")],
            [InlineKeyboardButton(text=TEXTS[lang]["edit_btn"], callback_data="report_edit")],
        ]
    )

def kb_edit_choice(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["edit_link_btn"], callback_data="edit_link")],
            [InlineKeyboardButton(text=TEXTS[lang]["edit_reason_btn"], callback_data="edit_reason")],
            [InlineKeyboardButton(text=TEXTS[lang]["edit_proof_btn"], callback_data="edit_proof")],
            [InlineKeyboardButton(text=TEXTS[lang]["back_btn"], callback_data="back_to_summary")],
        ]
    )

# ============ ВСПОМОГАТЕЛЬНОЕ ============

async def show_summary(bot: Bot, chat_id: int, msg_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    if data.get("proof_type") == "photo":
        proof_display = TEXTS[lang]["photo_placeholder"]
    else:
        proof_display = data.get("proof", "-")

    text = TEXTS[lang]["summary"].format(
        link=data.get("link", "-"),
        reason=data.get("reason", "-"),
        proof=proof_display,
    )
    await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id, reply_markup=kb_summary(lang))
    await state.set_state(ReportForm.confirm)

async def safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

# ============ ХЕНДЛЕРЫ: СТАРТ / ЯЗЫК / ПРОВЕРКА ============

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    sent = await message.answer(
        "Выберите язык: / Оберіть мову: / Choose language:",
        reply_markup=kb_language(),
    )
    await state.update_data(msg_id=sent.message_id)

@router.callback_query(F.data.startswith("lang_"))
async def process_lang(callback: CallbackQuery, state: FSMContext) -> None:
    lang = callback.data.split("_", 1)[1]
    await state.update_data(lang=lang)
    await callback.message.edit_text(TEXTS[lang]["confirm_bot"], reply_markup=kb_confirm_human(lang))
    await callback.answer()

@router.callback_query(F.data == "confirm_human")
async def process_confirm_human(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(TEXTS[lang]["main_menu"], reply_markup=kb_main_menu(lang))
    await callback.answer()

# ============ ХЕНДЛЕРЫ: НАЧАЛО ЖАЛОБЫ ============

@router.callback_query(F.data == "report_start")
async def process_report_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(TEXTS[lang]["enter_link"])
    await state.update_data(msg_id=callback.message.message_id)
    await state.set_state(ReportForm.link)
    await callback.answer()

@router.message(ReportForm.link, F.text)
async def process_link(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
    editing = data.get("editing", False)

    await state.update_data(link=message.text)
    await safe_delete(message)

    if editing:
        await state.update_data(editing=False)
        await show_summary(message.bot, message.chat.id, msg_id, state)
    else:
        await message.bot.edit_message_text(
            TEXTS[lang]["enter_reason"], chat_id=message.chat.id, message_id=msg_id
        )
        await state.set_state(ReportForm.reason)

@router.message(ReportForm.reason, F.text)
async def process_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
    editing = data.get("editing", False)

    await state.update_data(reason=message.text)
    await safe_delete(message)

    if editing:
        await state.update_data(editing=False)
        await show_summary(message.bot, message.chat.id, msg_id, state)
    else:
        await message.bot.edit_message_text(
            TEXTS[lang]["enter_proof"], chat_id=message.chat.id, message_id=msg_id
        )
        await state.set_state(ReportForm.proof)

@router.message(ReportForm.proof, F.photo)
async def process_proof_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    msg_id = data.get("msg_id")

    await state.update_data(proof=message.photo[-1].file_id, proof_type="photo", editing=False)
    await safe_delete(message)
    await show_summary(message.bot, message.chat.id, msg_id, state)

@router.message(ReportForm.proof, F.text)
async def process_proof_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    msg_id = data.get("msg_id")

    await state.update_data(proof=message.text, proof_type="text", editing=False)
    await safe_delete(message)
    await show_summary(message.bot, message.chat.id, msg_id, state)

# ============ ХЕНДЛЕРЫ: ИЗМЕНЕНИЕ ЖАЛОБЫ ============

@router.callback_query(F.data == "report_edit")
async def process_report_edit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await callback.message.edit_text(TEXTS[lang]["edit_choose"], reply_markup=kb_edit_choice(lang))
    await state.set_state(ReportForm.edit_choice)
    await callback.answer()

@router.callback_query(F.data == "back_to_summary")
async def process_back_to_summary(callback: CallbackQuery, state: FSMContext) -> None:
    await show_summary(callback.bot, callback.message.chat.id, callback.message.message_id, state)
    await callback.answer()

@router.callback_query(F.data.in_({"edit_link", "edit_reason", "edit_proof"}))
async def process_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    field = callback.data.split("_", 1)[1]

    prompts = {
        "link": TEXTS[lang]["enter_link"],
        "reason": TEXTS[lang]["enter_reason"],
        "proof": TEXTS[lang]["enter_proof"],
    }
    await callback.message.edit_text(prompts[field])
    await state.update_data(editing=True, msg_id=callback.message.message_id)
    await state.set_state(getattr(ReportForm, field))
    await callback.answer()

# ============ ХЕНДЛЕР: ПОДТВЕРЖДЕНИЕ И ОТПРАВКА ============

@router.callback_query(F.data == "report_confirm")
async def process_report_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    user = callback.from_user
    username = f"@{user.username}" if user.username else "нет юзернейма / no username"

    # Сохраняем жалобу
    report_data = {
        "user_id": user.id,
        "user_name": user.full_name,
        "username": username,
        "link": data.get('link', '-'),
        "reason": data.get('reason', '-'),
        "proof": data.get('proof', '-'),
        "proof_type": data.get('proof_type', 'text'),
        "date": datetime.now().isoformat(),
        "lang": lang,
        "status": "pending",  # pending, accepted, rejected
    }
    save_report(report_data)

    # Формируем сообщение для модераторов
    caption = f"🚨 Новая жалоба\n\nОт: {user.full_name} ({username})\nID: {user.id}\n\n🔗 Канал/бот: {data.get('link', '-')}\n📝 Причина: {data.get('reason', '-')}\n"

    # Кнопки для модераторов
    kb_moderator = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять жалобу", callback_data=f"accept_{user.id}"),
            ],
            [
                InlineKeyboardButton(text="❌ Отклонить жалобу", callback_data=f"reject_{user.id}"),
            ],
        ]
    )

    try:
        if data.get("proof_type") == "photo":
            caption += "📎 Доказательство: см. фото"
            await callback.bot.send_photo(MOD_CHAT_ID, data.get("proof"), caption=caption, reply_markup=kb_moderator)
        else:
            caption += f"📎 Доказательство: {data.get('proof', '-')}"
            await callback.bot.send_message(MOD_CHAT_ID, caption, reply_markup=kb_moderator)
    except Exception as e:
        logging.error("Не удалось отправить жалобу в чат модераторов: %s", e)

    await callback.message.edit_text(TEXTS[lang]["sent"])
    await state.clear()
    await state.update_data(lang=lang)
    await callback.answer()

# ============ ОБРАБОТЧИКИ КНОПОК МОДЕРАТОРОВ ============

@router.callback_query(F.data.startswith("accept_"))
async def accept_report(callback: CallbackQuery) -> None:
    """Принимает жалобу (зеленая кнопка)"""
    user_id = int(callback.data.split("_")[1])
    
    # Получаем данные пользователя из жалобы
    reports = load_reports()
    user_lang = "ru"
    for report in reports:
        if report['user_id'] == user_id:
            user_lang = report.get('lang', 'ru')
            break
    
    # Обновляем статус
    update_report_status(user_id, "accepted")
    
    # Отвечаем модератору
    await callback.answer("✅ Жалоба принята", show_alert=True)
    
    # Меняем сообщение в чате модераторов
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ **Жалоба ПРИНЯТА модератором**",
            parse_mode="Markdown"
        )
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logging.error("Ошибка при принятии: %s", e)
    
    # Отправляем уведомление пользователю
    try:
        await callback.bot.send_message(
            user_id,
            TEXTS[user_lang]["report_accepted"]
        )
    except Exception as e:
        logging.error("Не удалось отправить уведомление пользователю %s: %s", user_id, e)

@router.callback_query(F.data.startswith("reject_"))
async def reject_report(callback: CallbackQuery) -> None:
    """Отклоняет жалобу (красная кнопка)"""
    user_id = int(callback.data.split("_")[1])
    
    # Получаем данные пользователя из жалобы
    reports = load_reports()
    user_lang = "ru"
    for report in reports:
        if report['user_id'] == user_id:
            user_lang = report.get('lang', 'ru')
            break
    
    # Обновляем статус
    update_report_status(user_id, "rejected")
    
    # Отвечаем модератору
    await callback.answer("❌ Жалоба отклонена", show_alert=True)
    
    # Меняем сообщение в чате модераторов
    try:
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ **Жалоба ОТКЛОНЕНА модератором**",
            parse_mode="Markdown"
        )
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logging.error("Ошибка при отклонении: %s", e)
    
    # Отправляем уведомление пользователю
    try:
        await callback.bot.send_message(
            user_id,
            TEXTS[user_lang]["report_rejected"]
        )
    except Exception as e:
        logging.error("Не удалось отправить уведомление пользователю %s: %s", user_id, e)

# ============ КОМАНДА ДЛЯ МОДЕРАТОРОВ: /reports ============

@router.message(Command("reports"))
async def cmd_reports(message: Message, state: FSMContext) -> None:
    """Показывает список всех жалоб за последние 7 дней"""
    # Проверяем, что сообщение из чата модераторов
    if message.chat.id != MOD_CHAT_ID:
        return

    lang = "ru"
    reports = load_reports()
    
    # Фильтруем жалобы за последние 7 дней
    cutoff = datetime.now() - timedelta(days=7)
    recent_reports = [r for r in reports if datetime.fromisoformat(r['date']) > cutoff]
    
    if not recent_reports:
        await message.answer(TEXTS[lang]["no_reports"])
        return
    
    # Формируем список
    text = TEXTS[lang]["reports_title"]
    for i, report in enumerate(recent_reports[-10:], 1):  # Показываем по
