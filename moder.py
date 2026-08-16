"""
Telegram-бот для приёма репортов с модерацией
Работает на webhook через порт 10000 для Render.com
"""

import asyncio
import logging
import math
import os
import random
import json
import sys
from datetime import datetime

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    Update,
    FSInputFile,
)

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ReportRequest
from telethon.tl.types import (
    InputReportReasonSpam,
    InputReportReasonViolence,
    InputReportReasonChildAbuse,
    InputReportReasonPornography,
    InputReportReasonIllegalDrugs,
    InputReportReasonCopyright,
    InputReportReasonPersonalDetails,
    InputReportReasonFake,
)

# ============ НАСТРОЙКИ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не установлен!")
    sys.exit(1)

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
if not API_ID or not API_HASH:
    print("⚠️ ВНИМАНИЕ: API_ID / API_HASH не заданы — функция отправки жалоб (/block) работать не будет")

SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "sessions.json")

WEBHOOK_PATH = "/webhook"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "10000"))

# ID чата модераторов
MOD_CHAT_ID = -1004455021885

# Кулдаун между репортами (в минутах)
COOLDOWN_MINUTES = 30
COOLDOWN_SECONDS = COOLDOWN_MINUTES * 60

# Путь к аватарке бота - JPG (будет отправляться в каждом сообщении)
BOT_AVATAR = os.path.join(os.path.dirname(__file__), "ava.jpg")

# Проверяем существование файла
if os.path.exists(BOT_AVATAR):
    print(f"✅ Аватарка найдена: {BOT_AVATAR}")
else:
    print(f"⚠️ Аватарка не найдена: {BOT_AVATAR}")
    BOT_AVATAR = None

# ============ ЛОГИРОВАНИЕ ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

CODE_VERSION = "2026-08-08-session-strings-v1"
logger.info(f"📦 Версия кода: {CODE_VERSION}")

# ============ ХРАНИЛИЩА ============

REPORTS: dict[int, dict] = {}
_REPORT_COUNTER = 0

USER_PREFS: dict[int, dict] = {}

LAST_REPORT_TIME: dict[int, datetime] = {}

# Структура бана: user_id -> {"reason": str, "permanent": bool, "appeal_sent": bool, "name": str, "lang": str}
BANNED_USERS: dict[int, dict] = {}

COOLDOWN_ENABLED: bool = True
BOT_ENABLED: bool = True  # Глобальный выключатель бота

QUESTIONS: dict[int, dict] = {}
_QUESTION_COUNTER = 0

APPEALS: dict[int, dict] = {}
_APPEAL_COUNTER = 0

# Список всех пользователей для рассылки
ALL_USERS: set[int] = set()

# Хранилище личных сообщений для модераторов
USER_MESSAGES: dict[int, dict] = {}
_USER_MSG_COUNTER = 0

# Кулдаун на кнопки (3 секунды)
BUTTON_COOLDOWN: dict[int, datetime] = {}

# ============ TELETHON: АККАУНТЫ ДЛЯ ОТПРАВКИ ЖАЛОБ ============

# name (session1, session2, ...) -> TelegramClient
BOTREPORT_SESSIONS: dict[str, TelegramClient] = {}

# Активные задачи отправки жалоб / флаги остановки: task_id (str chat_id) -> ...
active_tasks: dict[str, asyncio.Task] = {}
stop_flags: dict[str, bool] = {}

REASON_MAP = {
    '1': (InputReportReasonSpam(), "This message is a spam mailing. This user is sending spam mailings. This violates the Terms Of Service"),
    '2': (InputReportReasonViolence(), "This message contains or promotes extreme violence and threat actions."),
    '3': (InputReportReasonChildAbuse(), "Child abuse media content inside the attachment."),
    '4': (InputReportReasonPornography(), "Unconsensual pornographic materials or explicit public exposure."),
    '5': (InputReportReasonIllegalDrugs(), "Selling, distribution or trading forbidden illegal toxic substances or drugs."),
    '6': (InputReportReasonCopyright(), "This post infringes intellectual property and DMCA copyright rules."),
    '7': (InputReportReasonPersonalDetails(), "My personal data. Public dox profile containing private residential and state identity credentials."),
    '8': (InputReportReasonFake(), "This entity spreads fake non-authentic manipulation elements to misinform users."),
}

REASON_DISPLAY = {
    '1': "🚫 Спам",
    '2': "⚔️ Насилие",
    '3': "🚸 Детская порнография",
    '4': "🔞 Порнография",
    '5': "💊 Наркотики",
    '6': "©️ Авторские права",
    '7': "🆔 Личные данные",
    '8': "🎭 Фейк",
}

def load_sessions_from_disk() -> dict:
    if not os.path.exists(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Не удалось прочитать {SESSIONS_FILE}: {e}")
        return {}

def save_sessions_to_disk(sessions_dict: dict) -> None:
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Не удалось сохранить {SESSIONS_FILE}: {e}")

def next_session_name() -> str:
    """Возвращает следующее свободное имя вида session1, session2, ..."""
    i = 1
    while f"session{i}" in BOTREPORT_SESSIONS:
        i += 1
    return f"session{i}"

def get_sessions_by_name(pattern: str = "session") -> list:
    """Возвращает список (имя, клиент) для сессий, имя которых начинается с pattern"""
    return [(name, cl) for name, cl in BOTREPORT_SESSIONS.items() if name.startswith(pattern)]

async def load_sessions_on_startup() -> None:
    loaded = 0
    total = 0

    # 1) Сессии из переменной окружения SESSION_STRINGS (каждая строка — отдельная сессия)
    env_raw = os.getenv("SESSION_STRINGS", "")
    env_lines = [ln.strip() for ln in env_raw.replace(",", "\n").splitlines() if ln.strip()]
    for session_string in env_lines:
        total += 1
        name = next_session_name()
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"⚠️ Сессия из SESSION_STRINGS ({name}) не авторизована, пропускаю")
                continue
            client.flood_sleep_threshold = 0
            BOTREPORT_SESSIONS[name] = client
            loaded += 1
        except Exception as e:
            logger.error(f"⚠️ Не удалось подключить сессию из SESSION_STRINGS ({name}): {e}")
    if env_lines:
        logger.info(f"✅ Из SESSION_STRINGS загружено: {loaded}/{len(env_lines)}")

    # 2) Сессии, добавленные ранее через /block → sessions.json
    raw = load_sessions_from_disk()
    for saved_name, session_string in raw.items():
        total += 1
        name = saved_name if saved_name not in BOTREPORT_SESSIONS else next_session_name()
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"⚠️ Сессия {name} не авторизована, пропускаю")
                continue
            client.flood_sleep_threshold = 0
            BOTREPORT_SESSIONS[name] = client
            loaded += 1
        except Exception as e:
            logger.error(f"⚠️ Не удалось подключить сессию {name}: {e}")

    logger.info(f"✅ Всего загружено сессий для жалоб: {loaded}/{total}")

async def send_message_report(client, peer, message_id, reason_object, comment_text):
    try:
        await asyncio.wait_for(
            client(ReportRequest(
                peer=peer,
                id=[message_id],
                reason=reason_object,
                message=comment_text
            )),
            timeout=20
        )
        return True
    except Exception as error:
        logger.error(f"[ERR] Ошибка при отправке жалобы на сообщение: {error}")
        return False

async def send_peer_report(client, peer, reason_object, comment_text):
    try:
        await asyncio.wait_for(
            client(ReportRequest(
                peer=peer,
                id=[],
                reason=reason_object,
                message=comment_text
            )),
            timeout=20
        )
        return True
    except Exception as error:
        logger.error(f"[ERR] Ошибка при отправке жалобы на профиль: {error}")
        return False

async def generate_variations(text: str) -> list:
    variations = [
        text,
        text + " Please investigate this issue.",
        text + " This is a serious violation.",
        text + " I request immediate action.",
        text + " This violates Telegram's Terms of Service.",
        text + " Please take appropriate measures.",
        text + " This is unacceptable.",
        text + " I urge you to review this content.",
    ]
    random.shuffle(variations)
    return variations[:5]

def render_progressbar(current, total, length=20):
    percent = (current / total) if total else 0
    filled = int(length * percent)
    bar = '▓' * filled + '░' * (length - filled)
    return f"[{bar}] {percent*100:.1f}%"

def next_report_id() -> int:
    global _REPORT_COUNTER
    _REPORT_COUNTER += 1
    return _REPORT_COUNTER

def next_question_id() -> int:
    global _QUESTION_COUNTER
    _QUESTION_COUNTER += 1
    return _QUESTION_COUNTER

def next_appeal_id() -> int:
    global _APPEAL_COUNTER
    _APPEAL_COUNTER += 1
    return _APPEAL_COUNTER

def next_user_msg_id() -> int:
    global _USER_MSG_COUNTER
    _USER_MSG_COUNTER += 1
    return _USER_MSG_COUNTER

def get_cooldown_remaining_minutes(user_id: int) -> int:
    if not COOLDOWN_ENABLED:
        return 0
    last = LAST_REPORT_TIME.get(user_id)
    if not last:
        return 0
    elapsed = (datetime.utcnow() - last).total_seconds()
    remaining = COOLDOWN_SECONDS - elapsed
    if remaining <= 0:
        return 0
    return math.ceil(remaining / 60)

def detect_target_type(link: str) -> str:
    raw = link.strip()
    bare = raw.lstrip("@")
    if bare.lower().endswith("bot") and "/" not in bare and "." not in bare:
        return "bot"
    lowered = raw.lower()
    if lowered.startswith("https://t.me/") or lowered.startswith("http://t.me/") or lowered.startswith("t.me/"):
        tail = lowered.rsplit("/", 1)[-1]
        if tail.endswith("bot"):
            return "bot"
        return "channel_chat"
    return "site"

def check_button_cooldown(user_id: int) -> bool:
    """Проверяет, не нажат ли пользователь кнопку слишком часто (3 секунды кулдаун)"""
    last = BUTTON_COOLDOWN.get(user_id)
    if not last:
        BUTTON_COOLDOWN[user_id] = datetime.utcnow()
        return True
    elapsed = (datetime.utcnow() - last).total_seconds()
    if elapsed < 3:
        return False
    BUTTON_COOLDOWN[user_id] = datetime.utcnow()
    return True

# ============ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОТПРАВКИ С ФОТО ============

async def send_with_photo(target, text: str, reply_markup=None, edit_msg_id: int = None, chat_id: int = None) -> Message:
    """Отправляет сообщение с фото (если фото есть) или просто текстом."""
    bot = target.bot
    target_chat_id = chat_id or target.chat.id
    has_avatar = bool(BOT_AVATAR and os.path.exists(BOT_AVATAR))

    if edit_msg_id:
        try:
            if has_avatar:
                photo = FSInputFile(BOT_AVATAR)
                media = InputMediaPhoto(media=photo, caption=text)
                await bot.edit_message_media(
                    chat_id=target_chat_id,
                    message_id=edit_msg_id,
                    media=media,
                    reply_markup=reply_markup,
                )
            else:
                await bot.edit_message_text(
                    text=text,
                    chat_id=target_chat_id,
                    message_id=edit_msg_id,
                    reply_markup=reply_markup,
                )
            return None
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение {edit_msg_id}: {e}")
            try:
                await bot.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=edit_msg_id,
                    caption=text,
                    reply_markup=reply_markup,
                )
                return None
            except Exception as e2:
                logger.warning(f"Не удалось отредактировать подпись {edit_msg_id}: {e2}")

    try:
        if has_avatar:
            photo = FSInputFile(BOT_AVATAR)
            return await bot.send_photo(target_chat_id, photo=photo, caption=text, reply_markup=reply_markup)
        return await bot.send_message(target_chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение в чат {target_chat_id}: {e}")
        return None

async def _auto_delete_message(bot: Bot, chat_id: int, message_id: int, delay: int = 10) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def edit_mod_message(bot: Bot, chat_id: int, message_id: int, text: str, reply_markup=None) -> None:
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        return
    except Exception:
        pass
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение модераторов {message_id}: {e}")
      # ============ ТЕКСТЫ ============

TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык:",
        "confirm_bot": "Подтвердите, что вы не робот:",
        "confirm_bot_btn": "Я не робот",
        "main_menu": "Выберите действие:",
        "report_btn": "📝 Репорт",
        "question_btn": "❓ Вопрос",
        "my_reports_btn": "📄 Мои репорты",
        "my_questions_btn": "❔ Мои вопросы",
        "instruction_btn": "ℹ️ Инструкция",
        "instruction_text": (
            "ℹ️ Как пользоваться ботом:\n\n"
            "1️⃣ После выбора языка и проверки на бота вас встречает главное меню. "
            "В главном меню можно выбирать действия («Репорт» и «Вопрос»), "
            "также под ними есть кнопки «Мои репорты» и «Мои вопросы», "
            "там вы можете увидеть ответы на ваши репорты и вопросы.\n\n"
            "2️⃣ Нажимая на кнопку «Репорт» вы должны ввести ссылку на материал, "
            "после этого кратко описать что нарушает этот материал.\n\n"
            "3️⃣ Нажимая на кнопку «Вопрос» вы должны кратко описать в тексте свой вопрос.\n\n"
            "4️⃣ Репорт/Вопрос уйдёт модераторам бота — дождитесь решения, "
            "статус можно проверить в разделе «Мои репорты»/«Мои вопросы»."
        ),
        "enter_link": "Введите ссылку на нарушающий материал:",
        "enter_reason": "Опишите суть репорта:",
        "sent": "Ваш репорт #{id} отправлен на рассмотрение.",
        "back_btn": "⬅️ Вернуться",
        "approved": "✅ Ваш репорт #{id} принят",
        "rejected": "❌ Ваш репорт #{id} отклонён",
        "rejected_with_reason": "❌ Ваш репорт #{id} отклонён.\n\nПричина: {reason}",
        "cooldown": "⏳ Действует кулдаун. Подождите {minutes} мин.",
        "my_reports_title": "📋 Ваши репорты:",
        "my_reports_empty": "У вас пока нет репортов.",
        "my_reports_item_pending": "⏳ Репорт #{id}",
        "my_reports_item_done": "🔔 Репорт #{id}",
        "my_reports_not_found": "Репорт не найден",
        "my_questions_title": "📋 Ваши вопросы:",
        "my_questions_empty": "У вас пока нет вопросов.",
        "my_questions_item_pending": "⏳ Вопрос #{id}",
        "my_questions_item_done": "🔔 Вопрос #{id}",
        "my_questions_not_found": "Вопрос не найден",
        "my_questions_status_pending": "⏳ Вопрос #{id}\n\n{text}\n\nСтатус: ожидает ответа модератора",
        "my_questions_status_answered": "✅ Вопрос #{id}\n\n{text}\n\nОтвет модератора:\n{answer}",
        "my_reports_status_pending": "⏳ Репорт #{id}\n\nСтатус: на рассмотрении",
        "my_reports_status_approved": "✅ Репорт #{id} принят и будет рассматриваться модераторами",
        "my_reports_status_rejected": "❌ Ваш репорт #{id} был отклонён",
        "my_reports_status_rejected_with_reason": "❌ Ваш репорт #{id} был отклонён.\n\nПричина: {reason}",
        "my_reports_status_blocked": "🚫 Репорт #{id}\n\nСтатус: {type} заблокирован",
        "blocked_btn": "🚫 Заблокировано",
        "blocked_sent": "✅ Уведомление о блокировке отправлено пользователю",
        "blocked_notification": "🚫 Материал заблокирован!\n\nВаш репорт #{id} был рассмотрен и материал действительно нарушает правила. Благодарим за помощь!",
        "banned": "🚫 Вы заблокированы!\n\nХотите подать апелляцию?",
        "banned_permanent": "🚫 Ваша апелляция отклонена. Вы больше не можете пользоваться ботом.",
        "banned_by_admins": "🚫 Вас заблокировали администраторы бота.",
        "bot_disabled": "🔧 Ведутся технические работы. Пожалуйста, попробуйте позже.",
        "appeal_btn_yes": "✅ Да, подать апелляцию",
        "appeal_btn_no": "❌ Нет",
        "appeal_prompt": "✏️ Напишите текст апелляции:\n(Объясните почему вас стоит разблокировать)",
        "appeal_sent": "Ваша апелляция отправлена на рассмотрение.",
        "appeal_notification": "📨 Новая апелляция\n\nПользователь: {name} (@{username})\nID: {user_id}\n\nТекст апелляции:\n{text}",
        "appeal_approved": "✅ Ваша апелляция одобрена! Вы разблокированы.",
        "appeal_rejected": "❌ Ваша апелляция отклонена. Вы больше не сможете пользоваться ботом.",
        "appeal_already_sent": "Вы уже отправили апелляцию. Ожидайте решения модераторов.",
        "unbanned": "✅ Вы были разблокированы модераторами.",
        "reject_reason_prompt": "✏️ Напишите причину отказа для пользователя:",
        "reject_reason_cancel": "Отмена",
        "question_prompt": "Введите ваш вопрос:",
        "question_sent": "Ваш вопрос #{id} отправлен. Ждите ответа.",
        "question_notification": "❓ Новый вопрос #{id}\n\nОт: {name} (@{username})\nID: {user_id}\n\nВопрос: {text}",
        "question_reply_prompt": "✏️ Напишите ответ на вопрос #{id}:",
        "question_reply_format": "Ответ на ваш вопрос #{id}: {answer}",
        "cooldown_disabled": "Кулдаун отключен",
        "cooldown_enabled": "Кулдаун включен",
        "bot_disabled_msg": "Бот отключен",
        "bot_enabled_msg": "Бот включен",
        "banned_list_empty": "📋 Список забаненных пользователей пуст",
        "banned_list_title": "📋 Список забаненных пользователей:",
        "banned_list_item": "ID: {user_id}\nИмя: {name}\nПричина: {reason}\nСтатус: {status}",
        "unban_success": "✅ Пользователь разбанен",
        "unban_fail": "❌ Пользователь не найден в списке бана",
        "broadcast_prompt": "✏️ Введите текст для рассылки всем пользователям:",
        "broadcast_sent": "✅ Рассылка отправлена {count} пользователям",
        "broadcast_fail": "❌ Ошибка при отправке рассылки",
        "help_text": (
            "📋 Список команд модератора:\n\n"
            "/reports - Список активных репортов\n"
            "/banned - Список забаненных пользователей\n"
            "/off - Отключить бота (технические работы)\n"
            "/on - Включить бота\n"
            "/offkd - Отключить кулдаун\n"
            "/onkd - Включить кулдаун\n"
            "/kdstatus - Статус кулдауна\n"
            "/send - Сделать рассылку всем пользователям\n"
            "/msg - Написать пользователю по ID\n"
            "/block - Меню отправки жалоб в Telegram (аккаунты)\n"
            "/info - Меню помощи\n"
            "/help - Показать это сообщение"
        ),
        "msg_prompt": "✏️ Введите ID пользователя и текст сообщения через пробел:\nПример: 123456789 Привет!",
        "msg_sent": "✅ Сообщение отправлено пользователю {user_id}",
        "msg_fail": "❌ Не удалось отправить сообщение пользователю {user_id}",
        "msg_invalid": "❌ Неверный формат. Используйте: /msg ID Текст",
        "msg_from_mod": "📨 Сообщение от модератора:\n\n{text}",
        "info_text": (
    "📜 СПИСОК ВСЕХ КОМАНД ЮЗЕРБОТА\n\n"
    "📜 Режим повторения:\n"
    "/povtor — Включить режим повторения для botreport сессий.\n"
    "/unpovtor — Выключить режим повторения.\n\n"
    "🤖 Модуль Искусственного Интеллекта:\n"
    ".ai <запрос> — Прямой вопрос к нейросети (ответ строго на русском).\n"
    ".anal <ссылка> — Анализ канала на нарушения.\n\n"
    "🛑 Жалобы и репорты:\n"
    ".botreport <юзернейм> <причина> [текст_жалобы] — Репорт на бота.\n"
    ".linkreport <канал> <сообщение> <причина> [текст_жалобы] — Репорт по ссылке.\n"
    ".mail <email> <тема> <текст_жалобы> — Отправка письма.\n\n"
    "📊 Памятка кодов причин:\n"
    "1 — Спам и несанкционированная реклама\n"
    "2 — Насилие, угрозы физической расправы\n"
    "3 — Детский абьюз и порнография (CSAM)\n"
    "4 — Порнографические материалы без согласия\n"
    "5 — Продажа/распространение наркотиков\n"
    "6 — Нарушение авторских прав (АП / DMCA)\n"
    "7 — Персональные данные (Доксинг, слив PII)\n"
    "8 — Фейк-аккаунты, дезинформация"
),
        "button_cooldown": "⏳ Подождите 3 секунды перед следующим действием",
    },
      "ua": {
        "choose_lang": "Оберіть мову:",
        "confirm_bot": "Підтвердіть, що ви не робот:",
        "confirm_bot_btn": "Я не робот",
        "main_menu": "Виберіть дію:",
        "report_btn": "📝 Репорт",
        "question_btn": "❓ Питання",
        "my_reports_btn": "📄 Мої репорти",
        "my_questions_btn": "❔ Мої питання",
        "instruction_btn": "ℹ️ Інструкція",
        "instruction_text": (
            "ℹ️ Як користуватися ботом:\n\n"
            "1️⃣ Після вибору мови та перевірки на бота вас зустрічає головне меню. "
            "У головному меню можна вибирати дії («Репорт» та «Питання»), "
            "також під ними є кнопки «Мої репорти» та «Мої питання», "
            "там ви можете побачити відповіді на ваші репорти та питання.\n\n"
            "2️⃣ Натискаючи на кнопку «Репорт» ви повинні ввести посилання на матеріал, "
            "після цього коротко описати що порушує цей матеріал.\n\n"
            "3️⃣ Натискаючи на кнопку «Питання» ви повинні коротко описати в тексті своє питання.\n\n"
            "4️⃣ Репорт/Питання піде модераторам бота — дочекайтеся рішення, "
            "статус можна перевірити в розділі «Мої репорти»/«Мої питання»."
        ),
        "enter_link": "Введіть посилання на матеріал, що порушує правила:",
        "enter_reason": "Опишіть суть репорту:",
        "sent": "Ваш репорт #{id} надіслано на розгляд.",
        "back_btn": "⬅️ Повернутися",
        "approved": "✅ Ваш репорт #{id} прийнято",
        "rejected": "❌ Ваш репорт #{id} відхилено",
        "rejected_with_reason": "❌ Ваш репорт #{id} відхилено.\n\nПричина: {reason}",
        "cooldown": "⏳ Діє кулдаун. Зачекайте {minutes} хв.",
        "my_reports_title": "📋 Ваші репорти:",
        "my_reports_empty": "У вас поки немає репортів.",
        "my_reports_item_pending": "⏳ Репорт #{id}",
        "my_reports_item_done": "🔔 Репорт #{id}",
        "my_reports_not_found": "Репорт не знайдено",
        "my_questions_title": "📋 Ваші питання:",
        "my_questions_empty": "У вас поки немає питань.",
        "my_questions_item_pending": "⏳ Питання #{id}",
        "my_questions_item_done": "🔔 Питання #{id}",
        "my_questions_not_found": "Питання не знайдено",
        "my_questions_status_pending": "⏳ Питання #{id}\n\n{text}\n\nСтатус: очікує відповіді модератора",
        "my_questions_status_answered": "✅ Питання #{id}\n\n{text}\n\nВідповідь модератора:\n{answer}",
        "my_reports_status_pending": "⏳ Репорт #{id}\n\nСтатус: на розгляді",
        "my_reports_status_approved": "✅ Репорт #{id} прийнято, його розглянуть модератори",
        "my_reports_status_rejected": "❌ Ваш репорт #{id} відхилено",
        "my_reports_status_rejected_with_reason": "❌ Ваш репорт #{id} відхилено.\n\nПричина: {reason}",
        "my_reports_status_blocked": "🚫 Репорт #{id}\n\nСтатус: {type} заблоковано",
        "blocked_btn": "🚫 Заблоковано",
        "blocked_sent": "✅ Повідомлення про блокування надіслано користувачу",
        "blocked_notification": "🚫 Матеріал заблоковано!\n\nВаш репорт #{id} було розглянуто і матеріал дійсно порушує правила. Дякуємо за допомогу!",
        "banned": "🚫 Вас заблоковано!\n\nХочете подати апеляцію?",
        "banned_permanent": "🚫 Вашу апеляцію відхилено. Ви більше не можете користуватися ботом.",
        "banned_by_admins": "🚫 Вас заблокували адміністратори бота.",
        "bot_disabled": "🔧 Ведуться технічні роботи. Будь ласка, спробуйте пізніше.",
        "appeal_btn_yes": "✅ Так, подати апеляцію",
        "appeal_btn_no": "❌ Ні",
        "appeal_prompt": "✏️ Напишіть текст апеляції:\n(Поясніть чому вас варто розблокувати)",
        "appeal_sent": "Вашу апеляцію надіслано на розгляд.",
        "appeal_notification": "📨 Нова апеляція\n\nКористувач: {name} (@{username})\nID: {user_id}\n\nТекст апеляції:\n{text}",
        "appeal_approved": "✅ Вашу апеляцію схвалено! Вас розблоковано.",
        "appeal_rejected": "❌ Вашу апеляцію відхилено. Ви більше не зможете користуватися ботом.",
        "appeal_already_sent": "Ви вже відправили апеляцію. Очікуйте рішення модераторів.",
        "unbanned": "✅ Вас розблокували модератори.",
        "reject_reason_prompt": "✏️ Напишіть причину відмови для користувача:",
        "reject_reason_cancel": "Скасування",
        "question_prompt": "Введіть ваше питання:",
        "question_sent": "Ваше питання #{id} надіслано. Чекайте відповіді.",
        "question_notification": "❓ Нове питання #{id}\n\nВід: {name} (@{username})\nID: {user_id}\n\nПитання: {text}",
        "question_reply_prompt": "✏️ Напишіть відповідь на питання #{id}:",
        "question_reply_format": "Відповідь на ваше питання #{id}: {answer}",
        "cooldown_disabled": "Кулдаун вимкнено",
        "cooldown_enabled": "Кулдаун увімкнено",
        "bot_disabled_msg": "Бот вимкнено",
        "bot_enabled_msg": "Бот увімкнено",
        "banned_list_empty": "📋 Список заблокованих користувачів порожній",
        "banned_list_title": "📋 Список заблокованих користувачів:",
        "banned_list_item": "ID: {user_id}\nІм'я: {name}\nПричина: {reason}\nСтатус: {status}",
        "unban_success": "✅ Користувача розблоковано",
        "unban_fail": "❌ Користувача не знайдено в списку бана",
        "broadcast_prompt": "✏️ Введіть текст для розсилки всім користувачам:",
        "broadcast_sent": "✅ Розсилку надіслано {count} користувачам",
        "broadcast_fail": "❌ Помилка при надсиланні розсилки",
        "help_text": (
            "📋 Список команд модератора:\n\n"
            "/reports - Список активних репортів\n"
            "/banned - Список заблокованих користувачів\n"
            "/off - Вимкнути бота (технічні роботи)\n"
            "/on - Увімкнути бота\n"
            "/offkd - Вимкнути кулдаун\n"
            "/onkd - Увімкнути кулдаун\n"
            "/kdstatus - Статус кулдауну\n"
            "/send - Зробити розсилку всім користувачам\n"
            "/msg - Написати користувачеві по ID\n"
            "/block - Меню відправки скарг в Telegram (акаунти)\n"
            "/help - Показати це повідомлення"
        ),
        "msg_prompt": "✏️ Введіть ID користувача та текст повідомлення через пробіл:\nПриклад: 123456789 Привіт!",
        "msg_sent": "✅ Повідомлення надіслано користувачеві {user_id}",
        "msg_fail": "❌ Не вдалося надіслати повідомлення користувачеві {user_id}",
        "msg_invalid": "❌ Невірний формат. Використовуйте: /msg ID Текст",
        "msg_from_mod": "📨 Повідомлення від модератора:\n\n{text}",
        "button_cooldown": "⏳ Зачекайте 3 секунды перед наступною дією",
    },
    "en": {
        "choose_lang": "Choose language:",
        "confirm_bot": "Please confirm you're not a robot:",
        "confirm_bot_btn": "I'm not a robot",
        "main_menu": "Choose an action:",
        "report_btn": "📝 Report",
        "question_btn": "❓ Question",
        "my_reports_btn": "📄 My reports",
        "my_questions_btn": "❔ My questions",
        "instruction_btn": "ℹ️ Instructions",
        "instruction_text": (
            "ℹ️ How to use the bot:\n\n"
            "1️⃣ After choosing a language and verification, you'll see the main menu. "
            "In the main menu you can choose actions (\"Report\" and \"Question\"), "
            "also below them there are buttons \"My reports\" and \"My questions\", "
            "where you can see answers to your reports and questions.\n\n"
            "2️⃣ By clicking \"Report\" you must enter a link to the material, "
            "then briefly describe what violates this material.\n\n"
            "3️⃣ By clicking \"Question\" you must briefly describe your question.\n\n"
            "4️⃣ The Report/Question will be sent to the bot moderators — "
            "wait for a decision, you can check the status in the "
            "\"My reports\"/\"My questions\" section."
        ),
        "enter_link": "Enter the link to the violating content:",
        "enter_reason": "Describe the violation:",
        "sent": "Your report #{id} has been sent for review.",
        "back_btn": "⬅️ Back",
        "approved": "✅ Your report #{id} has been approved",
        "rejected": "❌ Your report #{id} has been rejected",
        "rejected_with_reason": "❌ Your report #{id} has been rejected.\n\nReason: {reason}",
        "cooldown": "⏳ Cooldown active. Please wait {minutes} min.",
        "my_reports_title": "📋 Your reports:",
        "my_reports_empty": "You don't have any reports yet.",
        "my_reports_item_pending": "⏳ Report #{id}",
        "my_reports_item_done": "🔔 Report #{id}",
        "my_reports_not_found": "Report not found",
        "my_questions_title": "📋 Your questions:",
        "my_questions_empty": "You don't have any questions yet.",
        "my_questions_item_pending": "⏳ Question #{id}",
        "my_questions_item_done": "🔔 Question #{id}",
        "my_questions_not_found": "Question not found",
        "my_questions_status_pending": "⏳ Question #{id}\n\n{text}\n\nStatus: waiting for moderator's reply",
        "my_questions_status_answered": "✅ Question #{id}\n\n{text}\n\nModerator's reply:\n{answer}",
        "my_reports_status_pending": "⏳ Report #{id}\n\nStatus: pending review",
        "my_reports_status_approved": "✅ Report #{id} has been approved and will be reviewed by moderators",
        "my_reports_status_rejected": "❌ Your report #{id} has been rejected",
        "my_reports_status_rejected_with_reason": "❌ Your report #{id} has been rejected.\n\nReason: {reason}",
        "my_reports_status_blocked": "🚫 Report #{id}\n\nStatus: {type} has been blocked",
        "blocked_btn": "🚫 Blocked",
        "blocked_sent": "✅ Blocked notification sent to the user",
        "blocked_notification": "🚫 Material blocked!\n\nYour report #{id} has been reviewed and the material indeed violates the rules. Thank you for your help!",
        "banned": "🚫 You are banned!\n\nDo you want to appeal?",
        "banned_permanent": "🚫 Your appeal has been rejected. You can no longer use the bot.",
        "banned_by_admins": "🚫 You have been blocked by the bot administrators.",
        "bot_disabled": "🔧 Technical work in progress. Please try again later.",
        "appeal_btn_yes": "✅ Yes, appeal",
        "appeal_btn_no": "❌ No",
        "appeal_prompt": "✏️ Write your appeal:\n(Explain why you should be unbanned)",
        "appeal_sent": "Your appeal has been sent for review.",
        "appeal_notification": "📨 New appeal\n\nUser: {name} (@{username})\nID: {user_id}\n\nAppeal text:\n{text}",
        "appeal_approved": "✅ Your appeal has been approved! You are unbanned.",
        "appeal_rejected": "❌ Your appeal has been rejected. You can no longer use the bot.",
        "appeal_already_sent": "You have already sent an appeal. Wait for the moderators' decision.",
        "unbanned": "✅ You have been unbanned by moderators.",
        "reject_reason_prompt": "✏️ Please enter the rejection reason for the user:",
        "reject_reason_cancel": "Cancel",
        "question_prompt": "Enter your question:",
        "question_sent": "Your question #{id} has been sent. Waiting for response.",
        "question_notification": "❓ New question #{id}\n\nFrom: {name} (@{username})\nID: {user_id}\n\nQuestion: {text}",
        "question_reply_prompt": "✏️ Please enter your answer to question #{id}:",
        "question_reply_format": "Answer to your question #{id}: {answer}",
        "cooldown_disabled": "Cooldown disabled",
        "cooldown_enabled": "Cooldown enabled",
        "bot_disabled_msg": "Bot disabled",
        "bot_enabled_msg": "Bot enabled",
        "banned_list_empty": "📋 Banned users list is empty",
        "banned_list_title": "📋 Banned users list:",
        "banned_list_item": "ID: {user_id}\nName: {name}\nReason: {reason}\nStatus: {status}",
        "unban_success": "✅ User unbanned",
        "unban_fail": "❌ User not found in ban list",
        "broadcast_prompt": "✏️ Enter the text to broadcast to all users:",
        "broadcast_sent": "✅ Broadcast sent to {count} users",
        "broadcast_fail": "❌ Error sending broadcast",
        "help_text": (
            "📋 Moderator commands:\n\n"
            "/reports - List of active reports\n"
            "/banned - List of banned users\n"
            "/off - Disable bot (maintenance)\n"
            "/on - Enable bot\n"
            "/offkd - Disable cooldown\n"
            "/onkd - Enable cooldown\n"
            "/kdstatus - Cooldown status\n"
            "/send - Broadcast to all users\n"
            "/msg - Send message to user by ID\n"
            "/block - Telegram report menu (accounts)\n"
            "/help - Show this message"
        ),
        "msg_prompt": "✏️ Enter user ID and message text separated by space:\nExample: 123456789 Hello!",
        "msg_sent": "✅ Message sent to user {user_id}",
        "msg_fail": "❌ Failed to send message to user {user_id}",
        "msg_invalid": "❌ Invalid format. Use: /msg ID Text",
        "msg_from_mod": "📨 Message from moderator:\n\n{text}",
        "button_cooldown": "⏳ Please wait 3 seconds before next action",
    },
}

TARGET_TYPE_NOUN = {
    "ru": {"bot": "бот", "channel_chat": "канал/чат", "site": "сайт"},
    "ua": {"bot": "бот", "channel_chat": "канал/чат", "site": "сайт"},
    "en": {"bot": "bot", "channel_chat": "channel/chat", "site": "website"},
}

# ============ СОСТОЯНИЯ ============

class ReportForm(StatesGroup):
    link = State()
    reason = State()

class QuestionForm(StatesGroup):
    question = State()

class ModForm(StatesGroup):
    waiting_reject_reason = State()
    waiting_question_reply = State()
    waiting_broadcast = State()

class AppealForm(StatesGroup):
    waiting_appeal_text = State()

class BlockForm(StatesGroup):
    waiting_session_strings = State()
    waiting_report_username = State()
    waiting_custom_text = State()

router = Router()

class ChatIdLoggerMiddleware(BaseMiddleware):
    """Логирует реальный chat.id любого сообщения из группы/супергруппы —
    не блокирует и не перехватывает обработку, просто пишет в лог и идёт дальше."""
    async def __call__(self, handler, event: Message, data):
        try:
            if event.chat and event.chat.type in ("group", "supergroup"):
                logger.info(
                    f"📩 Сообщение из группы: chat.id={event.chat.id} "
                    f"title={event.chat.title!r} username={event.chat.username!r} "
                    f"(текущий MOD_CHAT_ID={MOD_CHAT_ID})"
                )
        except Exception:
            pass
        return await handler(event, data)

router.message.middleware(ChatIdLoggerMiddleware())
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
            [InlineKeyboardButton(
                text=TEXTS[lang]["confirm_bot_btn"], 
                callback_data="confirm_human",
                style="primary"
            )]
        ]
    )

def kb_main_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TEXTS[lang]["report_btn"], 
                    callback_data="menu_report",
                    style="danger"
                ),
                InlineKeyboardButton(
                    text=TEXTS[lang]["question_btn"], 
                    callback_data="menu_question",
                    style="success"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=TEXTS[lang]["my_reports_btn"],
                    callback_data="menu_my_reports",
                    style="primary"
                ),
                InlineKeyboardButton(
                    text=TEXTS[lang]["my_questions_btn"],
                    callback_data="menu_my_questions",
                    style="primary"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=TEXTS[lang]["instruction_btn"],
                    callback_data="menu_instruction",
                    style="primary"
                ),
            ],
        ]
    )

def kb_my_reports_list(user_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = []
    user_reports = [(rid, r) for rid, r in REPORTS.items() if r["user_id"] == user_id]
    for rid, r in sorted(user_reports, key=lambda x: -x[0]):
        is_new = r.get("status") != "pending" and not r.get("viewed", False)
        label_key = "my_reports_item_done" if is_new else "my_reports_item_pending"
        style = "danger" if is_new else "primary"
        rows.append([InlineKeyboardButton(
            text=TEXTS[lang][label_key].format(id=rid),
            callback_data=f"my_report_view_{rid}",
            style=style
        )])
    if not rows:
        rows = [[InlineKeyboardButton(text=TEXTS[lang]["my_reports_empty"], callback_data="noop")]]
    rows.append([InlineKeyboardButton(
        text=TEXTS[lang]["back_btn"],
        callback_data="menu_back",
        style="primary"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_my_report_detail(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["back_btn"],
                callback_data="menu_my_reports",
                style="primary"
            )]
        ]
    )

def kb_my_questions_list(user_id: int, lang: str) -> InlineKeyboardMarkup:
    rows = []
    user_questions = [(qid, q) for qid, q in QUESTIONS.items() if q["user_id"] == user_id]
    for qid, q in sorted(user_questions, key=lambda x: -x[0]):
        is_new = q.get("answered", False) and not q.get("viewed", False)
        label_key = "my_questions_item_done" if is_new else "my_questions_item_pending"
        style = "danger" if is_new else "primary"
        rows.append([InlineKeyboardButton(
            text=TEXTS[lang][label_key].format(id=qid),
            callback_data=f"my_question_view_{qid}",
            style=style
        )])
    if not rows:
        rows = [[InlineKeyboardButton(text=TEXTS[lang]["my_questions_empty"], callback_data="noop")]]
    rows.append([InlineKeyboardButton(
        text=TEXTS[lang]["back_btn"],
        callback_data="menu_back",
        style="primary"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_my_question_detail(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["back_btn"],
                callback_data="menu_my_questions",
                style="primary"
            )]
        ]
    )

def kb_back_to_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["back_btn"], 
                callback_data="menu_back",
                style="primary"
            )]
        ]
    )

def kb_appeal(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=TEXTS[lang]["appeal_btn_yes"], 
                    callback_data="appeal_yes",
                    style="success"
                ),
                InlineKeyboardButton(
                    text=TEXTS[lang]["appeal_btn_no"], 
                    callback_data="appeal_no",
                    style="danger"
                ),
            ]
        ]
    )

def kb_moderator_actions(report_id: int, user_id: int) -> InlineKeyboardMarkup:
    banned = user_id in BANNED_USERS
    ban_btn = (
        InlineKeyboardButton(
            text="✅ Разбанить", 
            callback_data=f"mod_unban_{report_id}",
            style="success"
        )
        if banned
        else InlineKeyboardButton(
            text="🚫 Забанить", 
            callback_data=f"mod_ban_{report_id}",
            style="danger"
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять", 
                    callback_data=f"mod_approve_{report_id}",
                    style="success"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", 
                    callback_data=f"mod_reject_{report_id}",
                    style="danger"
                ),
            ],
            [ban_btn],
        ]
    )

def kb_reports_list() -> InlineKeyboardMarkup:
    rows = []
    for rid, r in sorted(REPORTS.items(), key=lambda x: -x[0]):
        if r["status"] == "rejected":
            continue
        dot = "🔴" if r["status"] == "pending" else "🟢"
        label = f"{dot} #{rid} — {r['username'] or r['full_name']}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"view_{rid}")])
    if not rows:
        rows = [[InlineKeyboardButton(text="Нет активных репортов", callback_data="noop")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_report_detail(report_id: int, user_id: int, status: str = "pending") -> InlineKeyboardMarkup:
    banned = user_id in BANNED_USERS
    ban_btn = (
        InlineKeyboardButton(
            text="✅ Разбанить", 
            callback_data=f"mod_unban_{report_id}",
            style="success"
        )
        if banned
        else InlineKeyboardButton(
            text="🚫 Забанить", 
            callback_data=f"mod_ban_{report_id}",
            style="danger"
        )
    )

    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton(
                text="✅ Принять", 
                callback_data=f"mod_approve_{report_id}",
                style="success"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить", 
                callback_data=f"mod_reject_{report_id}",
                style="danger"
            ),
        ])
    elif status == "approved":
        rows.append([
            InlineKeyboardButton(
                text="🚫 Сообщить о блокировке",
                callback_data=f"mod_blocked_{report_id}",
                style="danger"
            ),
        ])

    rows.append([ban_btn])
    rows.append([InlineKeyboardButton(
        text="⬅️ К списку", 
        callback_data="reports_list",
        style="primary"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_cancel_reject(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["reject_reason_cancel"], 
                callback_data="reject_cancel",
                style="primary"
            )]
        ]
    )

def kb_moderator_question_actions(qid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✏️ Ответить", 
                callback_data=f"mod_question_reply_{qid}",
                style="primary"
            )]
        ]
    )

def kb_cancel_question_reply(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["reject_reason_cancel"], 
                callback_data="question_reply_cancel",
                style="primary"
            )]
        ]
    )

def kb_appeal_actions(appeal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить", 
                    callback_data=f"appeal_approve_{appeal_id}",
                    style="success"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", 
                    callback_data=f"appeal_reject_{appeal_id}",
                    style="danger"
                ),
            ]
        ]
    )

def kb_banned_list() -> InlineKeyboardMarkup:
    rows = []
    for user_id, data in BANNED_USERS.items():
        label = f"{data.get('name', 'Unknown')} (ID: {user_id})"
        rows.append([InlineKeyboardButton(
            text=label, 
            callback_data=f"ban_unban_{user_id}",
            style="primary"
        )])
    if not rows:
        rows = [[InlineKeyboardButton(text="Список пуст", callback_data="noop")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_broadcast_cancel(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=TEXTS[lang]["reject_reason_cancel"], 
                callback_data="broadcast_cancel",
                style="primary"
            )]
        ]
    )

# ============ КЛАВИАТУРЫ БЛОКИРОВКИ (ЖАЛОБЫ В TELEGRAM) ============

def kb_block_menu() -> InlineKeyboardMarkup:
    n = len(BOTREPORT_SESSIONS)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить сессии", callback_data="block_add_sessions", style="success")],
            [InlineKeyboardButton(text=f"📋 Аккаунты ({n})", callback_data="block_list_sessions", style="primary")],
            [InlineKeyboardButton(text="🚀 Отправить жалобы", callback_data="block_send_reports", style="danger")],
        ]
    )

def kb_block_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="block_cancel", style="danger")]
        ]
    )

def kb_block_back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="block_back_to_menu", style="primary")]
        ]
    )

def kb_block_sessions_list() -> InlineKeyboardMarkup:
    rows = []
    for name in sorted(BOTREPORT_SESSIONS.keys()):
        rows.append([InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"block_del_session_{name}", style="danger")])
    if not rows:
        rows.append([InlineKeyboardButton(text="Аккаунтов пока нет", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="block_back_to_menu", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_report_reasons() -> InlineKeyboardMarkup:
    rows = []
    items = list(REASON_DISPLAY.items())
    for i in range(0, len(items), 2):
        row = []
        for key, label in items[i:i+2]:
            row.append(InlineKeyboardButton(text=label, callback_data=f"block_reason_{key}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="block_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_session_count() -> InlineKeyboardMarkup:
    available = len(BOTREPORT_SESSIONS)
    candidates = [1, 3, 5, 10, 20]
    rows = []
    row = []
    for n in candidates:
        if n >= available:
            continue
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"block_count_{n}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=f"✅ Все ({available})", callback_data="block_count_all", style="success")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="block_cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_report_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Свой текст жалобы", callback_data="block_custom_text", style="primary")],
            [InlineKeyboardButton(text="🚀 Отправить (стандартный текст)", callback_data="block_start_sending", style="success")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="block_cancel", style="danger")],
        ]
    )

def kb_report_stop() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Остановить", callback_data="block_stop_sending", style="danger")]
        ]
    )

# ============ ХЕЛПЕРЫ ============

async def cleanup_tracked(bot: Bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    ids = data.get("track_ids", [])
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
    await state.update_data(track_ids=[])

async def track(state: FSMContext, message_id: int) -> None:
    data = await state.get_data()
    ids = data.get("track_ids", [])
    ids.append(message_id)
    await state.update_data(track_ids=ids)

def report_caption(rid: int, r: dict) -> str:
    username = f"@{r['username']}" if r["username"] else "нет юзернейма"
    return (
        f"🚨 Новый репорт #{rid}\n\n"
        f"👤 От: {r['full_name']} ({username})\n"
        f"🆔 ID: {r['user_id']}\n\n"
        f"🔗 Ссылка: {r['link']}\n"
        f"📝 Текст: {r['reason']}"
    )

def appeal_caption(aid: int, a: dict) -> str:
    return (
        f"📨 Апелляция #{aid}\n\n"
        f"👤 Пользователь: {a['name']} (@{a['username']})\n"
        f"🆔 ID: {a['user_id']}\n\n"
        f"📝 Текст апелляции:\n{a['text']}"
             )
  # ============ ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ============

async def render_screen(message: Message, state: FSMContext, text: str, reply_markup=None) -> None:
    data = await state.get_data()
    screen_msg_id = data.get("screen_msg_id")
    sent = await send_with_photo(
        message,
        text,
        reply_markup=reply_markup,
        edit_msg_id=screen_msg_id,
        chat_id=message.chat.id
    )
    if sent:
        await state.update_data(screen_msg_id=sent.message_id)

async def show_main_menu(message: Message, state: FSMContext, edit: bool = False) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await render_screen(message, state, TEXTS[lang]["main_menu"], kb_main_menu(lang))

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    
    ALL_USERS.add(user_id)

    await cleanup_tracked(message.bot, message.chat.id, state)
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()

    if not BOT_ENABLED:
        lang = "ru"
        await send_with_photo(message, TEXTS[lang]["bot_disabled"])
        return

    prefs = USER_PREFS.get(user_id)
    lang = prefs["lang"] if prefs else "ru"
    await state.update_data(lang=lang)

    if user_id in BANNED_USERS and BANNED_USERS[user_id].get("permanent", False):
        if not BANNED_USERS[user_id].get("permanent_notified", False):
            await send_with_photo(message, TEXTS[lang]["banned_by_admins"])
            BANNED_USERS[user_id]["permanent_notified"] = True
        return

    if user_id in BANNED_USERS:
        sent = await send_with_photo(
            message,
            TEXTS[lang]["banned"],
            reply_markup=kb_appeal(lang)
        )
        if sent:
            await track(state, sent.message_id)
        return

    if prefs and prefs.get("confirmed"):
        await show_main_menu(message, state)
        logger.info(f"👤 Пользователь {user_id} запустил бота (повтор, lang={lang})")
        return

    sent = await send_with_photo(
        message,
        "Выберите язык / Оберіть мову / Choose language:",
        reply_markup=kb_language()
    )
    
    if sent:
        await track(state, sent.message_id)
        await state.update_data(screen_msg_id=sent.message_id)
    logger.info(f"👤 Пользователь {user_id} запустил бота")

@router.callback_query(F.data.startswith("lang_"))
async def process_lang(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 5 секунд"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    lang = callback.data.split("_", 1)[1]
    await state.update_data(lang=lang)
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["confirm_bot"],
        kb_confirm_human(lang)
    )

@router.callback_query(F.data == "confirm_human")
async def process_confirm_human(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")

    USER_PREFS[callback.from_user.id] = {"lang": lang, "confirmed": True}
    await state.update_data(lang=lang)
    
    await show_main_menu(callback.message, state)

@router.callback_query(F.data == "appeal_yes")
async def appeal_yes(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    user_id = callback.from_user.id
    
    if user_id not in BANNED_USERS:
        await send_with_photo(callback.message, "Вы не в бане")
        return
    
    if BANNED_USERS[user_id].get("permanent", False):
        await send_with_photo(callback.message, "Вы в вечном бане, апелляция невозможна")
        return
    
    if BANNED_USERS[user_id].get("appeal_sent", False):
        lang = BANNED_USERS[user_id].get("lang", "ru")
        await send_with_photo(callback.message, TEXTS[lang]["appeal_already_sent"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["appeal_prompt"],
        kb_back_to_menu(lang)
    )
    await state.set_state(AppealForm.waiting_appeal_text)

@router.callback_query(F.data == "appeal_no")
async def appeal_no(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    user_id = callback.from_user.id
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if user_id in BANNED_USERS:
        BANNED_USERS[user_id]["permanent"] = True
        BANNED_USERS[user_id]["appeal_sent"] = False
        BANNED_USERS[user_id]["permanent_notified"] = True
    
    await state.clear()
    
    await send_with_photo(callback.message, TEXTS[lang]["banned_by_admins"])
    logger.info(f"🚫 Пользователь {user_id} отказался от апелляции и отправлен в вечный бан")

@router.message(AppealForm.waiting_appeal_text, F.text)
async def process_appeal(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await send_with_photo(message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("screen_msg_id")
    appeal_text = message.text.strip()
    
    try:
        await message.delete()
    except Exception:
        pass
    
    user = message.from_user
    
    if user.id not in BANNED_USERS:
        await send_with_photo(message, "❌ Вы не в бане")
        await state.clear()
        return
    
    aid = next_appeal_id()
    APPEALS[aid] = {
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username or "нет юзернейма",
        "text": appeal_text,
        "lang": lang,
    }
    
    BANNED_USERS[user.id]["appeal_sent"] = True
    BANNED_USERS[user.id]["appeal_id"] = aid
    
    await send_with_photo(
        message,
        TEXTS[lang]["appeal_sent"],
        reply_markup=kb_back_to_menu(lang),
        edit_msg_id=msg_id,
        chat_id=message.chat.id
    )
    
    try:
        await message.bot.send_message(
            MOD_CHAT_ID,
            TEXTS[lang]["appeal_notification"].format(
                name=user.full_name,
                username=user.username or "нет юзернейма",
                user_id=user.id,
                text=appeal_text
            ),
            reply_markup=kb_appeal_actions(aid)
        )
        logger.info(f"📨 Апелляция #{aid} отправлена модераторам")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки апелляции модераторам: {e}")
    
    await state.clear()
    await state.update_data(lang=lang, screen_msg_id=msg_id)

@router.message(AppealForm.waiting_appeal_text)
async def process_appeal_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "menu_back")
async def menu_back(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    screen_msg_id = data.get("screen_msg_id")
    
    await state.clear()
    await state.update_data(lang=lang, screen_msg_id=screen_msg_id)
    
    await show_main_menu(callback.message, state)

@router.callback_query(F.data == "menu_report")
async def menu_report(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    if not BOT_ENABLED:
        await callback.answer()
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    user_id = callback.from_user.id
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if user_id in BANNED_USERS:
        await callback.answer()
        await send_with_photo(callback.message, TEXTS[lang]["banned"])
        return
    
    remaining = get_cooldown_remaining_minutes(user_id)
    if remaining > 0:
        await callback.answer(TEXTS[lang]["cooldown"].format(minutes=remaining), show_alert=True)
        return
    
    await callback.answer()
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["enter_link"],
        kb_back_to_menu(lang)
    )
    await state.set_state(ReportForm.link)

@router.callback_query(F.data == "menu_question")
async def menu_question(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if callback.from_user.id in BANNED_USERS:
        await send_with_photo(callback.message, TEXTS[lang]["banned"])
        return
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["question_prompt"],
        kb_back_to_menu(lang)
    )
    await state.set_state(QuestionForm.question)
  # ============ МОИ РЕПОРТЫ ============

@router.callback_query(F.data == "menu_my_reports")
async def menu_my_reports(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    user_id = callback.from_user.id
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["my_reports_title"],
        kb_my_reports_list(user_id, lang)
    )

@router.callback_query(F.data == "menu_my_questions")
async def menu_my_questions(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    user_id = callback.from_user.id
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["my_questions_title"],
        kb_my_questions_list(user_id, lang)
    )

@router.callback_query(F.data.startswith("my_question_view_"))
async def my_question_view(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    qid = int(callback.data.split("_")[-1])
    q = QUESTIONS.get(qid)
    
    if not q or q["user_id"] != callback.from_user.id:
        await render_screen(
            callback.message,
            state,
            TEXTS[lang]["my_questions_not_found"],
            kb_my_questions_list(callback.from_user.id, lang)
        )
        return
    
    # Отмечаем вопрос как просмотренный
    q["viewed"] = True
    
    if q.get("answered"):
        text = TEXTS[lang]["my_questions_status_answered"].format(
            id=qid, text=q["text"], answer=q.get("answer") or ""
        )
    else:
        text = TEXTS[lang]["my_questions_status_pending"].format(id=qid, text=q["text"])
    
    # Показываем детали вопроса
    await render_screen(
        callback.message,
        state,
        text,
        kb_my_question_detail(lang)
    )
    
    # Ждём 30 секунд, затем автоматически возвращаемся к списку вопросов
    async def return_to_list():
        await asyncio.sleep(30)
        current_state = await state.get_state()
        if current_state is None:
            return
        await render_screen(
            callback.message,
            state,
            TEXTS[lang]["my_questions_title"],
            kb_my_questions_list(callback.from_user.id, lang)
        )
    
    asyncio.create_task(return_to_list())

@router.callback_query(F.data == "menu_instruction")
async def menu_instruction(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    await render_screen(
        callback.message,
        state,
        TEXTS[lang]["instruction_text"],
        kb_back_to_menu(lang)
    )

@router.callback_query(F.data.startswith("my_report_view_"))
async def my_report_view(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    
    if not BOT_ENABLED:
        await send_with_photo(callback.message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    
    if not r or r["user_id"] != callback.from_user.id:
        await render_screen(
            callback.message,
            state,
            TEXTS[lang]["my_reports_not_found"],
            kb_my_reports_list(callback.from_user.id, lang)
        )
        return
    
    # Отмечаем репорт как просмотренный
    r["viewed"] = True
    
    status = r["status"]
    if status == "approved":
        text = TEXTS[lang]["my_reports_status_approved"].format(id=rid)
    elif status == "rejected":
        reject_reason = r.get("reject_reason", "")
        if reject_reason:
            text = TEXTS[lang]["my_reports_status_rejected_with_reason"].format(id=rid, reason=reject_reason)
        else:
            text = TEXTS[lang]["my_reports_status_rejected"].format(id=rid)
    elif status == "blocked":
        target_type = detect_target_type(r["link"])
        type_noun = TARGET_TYPE_NOUN.get(lang, TARGET_TYPE_NOUN["ru"]).get(target_type, "материал")
        text = TEXTS[lang]["my_reports_status_blocked"].format(id=rid, type=type_noun)
        # НЕ ОТПРАВЛЯЕМ В ЧАТ - только через кнопки "Мои репорты"
    else:
        text = TEXTS[lang]["my_reports_status_pending"].format(id=rid)
    
    # Показываем детали репорта
    await render_screen(
        callback.message,
        state,
        text,
        kb_my_report_detail(lang)
    )
    
    # Ждём 30 секунд, затем автоматически возвращаемся к списку репортов
    async def return_to_list():
        await asyncio.sleep(30)
        current_state = await state.get_state()
        if current_state is None:
            return
        await render_screen(
            callback.message,
            state,
            TEXTS[lang]["my_reports_title"],
            kb_my_reports_list(callback.from_user.id, lang)
        )
    
    asyncio.create_task(return_to_list())

# ============ ОБРАБОТКА ВВОДА ССЫЛКИ ============

@router.message(ReportForm.link, F.text)
async def process_link(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await send_with_photo(message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("screen_msg_id")

    await state.update_data(link=message.text.strip())

    try:
        await message.delete()
    except Exception:
        pass

    await send_with_photo(
        message,
        TEXTS[lang]["enter_reason"],
        reply_markup=kb_back_to_menu(lang),
        edit_msg_id=msg_id,
        chat_id=message.chat.id
    )
    
    await state.set_state(ReportForm.reason)

# ============ ОБРАБОТКА ВВОДА ПРИЧИНЫ ============

@router.message(ReportForm.reason, F.text)
async def process_reason(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await send_with_photo(message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("screen_msg_id")
    link = data.get("link")
    reason = message.text.strip()

    try:
        await message.delete()
    except Exception:
        pass

    user = message.from_user
    rid = next_report_id()
    REPORTS[rid] = {
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "link": link,
        "reason": reason,
        "lang": lang,
        "status": "pending",
        "mod_msg_id": None,
        "reject_reason": None,
        "viewed": False,
    }

    sent = await send_with_photo(
        message,
        TEXTS[lang]["sent"].format(id=rid),
        reply_markup=kb_back_to_menu(lang),
        edit_msg_id=msg_id,
        chat_id=message.chat.id
    )
    
    await state.clear()
    await state.update_data(lang=lang, screen_msg_id=sent.message_id if sent else msg_id)

    LAST_REPORT_TIME[user.id] = datetime.utcnow()

    try:
        if BOT_AVATAR and os.path.exists(BOT_AVATAR):
            photo = FSInputFile(BOT_AVATAR)
            sent_mod = await message.bot.send_photo(
                MOD_CHAT_ID,
                photo=photo,
                caption=report_caption(rid, REPORTS[rid]),
                reply_markup=kb_moderator_actions(rid, user.id)
            )
        else:
            sent_mod = await message.bot.send_message(
                MOD_CHAT_ID,
                report_caption(rid, REPORTS[rid]),
                reply_markup=kb_moderator_actions(rid, user.id)
            )
        REPORTS[rid]["mod_msg_id"] = sent_mod.message_id
        logger.info(f"✅ Репорт #{rid} отправлен модераторам с фото")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки модераторам: {e}")

# ============ ОБРАБОТКА ВВОДА ВОПРОСА ============

@router.message(QuestionForm.question, F.text)
async def process_question(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await send_with_photo(message, TEXTS["ru"]["bot_disabled"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("screen_msg_id")
    question_text = message.text.strip()

    try:
        await message.delete()
    except Exception:
        pass

    user = message.from_user
    qid = next_question_id()
    QUESTIONS[qid] = {
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username or "нет юзернейма",
        "text": question_text,
        "lang": lang,
        "answered": False,
        "answer": None,
        "viewed": False,
    }

    sent = await send_with_photo(
        message,
        TEXTS[lang]["question_sent"].format(id=qid),
        reply_markup=kb_back_to_menu(lang),
        edit_msg_id=msg_id,
        chat_id=message.chat.id
    )
    
    await state.clear()
    await state.update_data(lang=lang, screen_msg_id=sent.message_id if sent else msg_id)

    try:
        await message.bot.send_message(
            MOD_CHAT_ID,
            TEXTS[lang]["question_notification"].format(
                id=qid,
                name=user.full_name,
                username=user.username or "нет юзернейма",
                user_id=user.id,
                text=question_text
            ),
            reply_markup=kb_moderator_question_actions(qid)
        )
        logger.info(f"❓ Вопрос #{qid} отправлен модераторам")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки вопроса модераторам: {e}")
      # ============ ПАНЕЛЬ МОДЕРАТОРОВ ============

@router.message(Command("help"), F.chat.id == MOD_CHAT_ID)
async def cmd_help(message: Message) -> None:
    lang = "ru"
    await message.answer(TEXTS[lang]["help_text"])
    
@router.message(Command("info"), F.chat.id == MOD_CHAT_ID)
async def cmd_info(message: Message) -> None:
    lang = "ru"  # используем русский, т.к. текст только на русском
    await message.answer(TEXTS[lang]["info_text"])

@router.message(Command("reports"), F.chat.id == MOD_CHAT_ID)
async def cmd_reports(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("📋 Список активных репортов:", reply_markup=kb_reports_list())

@router.message(Command("banned"), F.chat.id == MOD_CHAT_ID)
async def cmd_banned(message: Message) -> None:
    if not BANNED_USERS:
        await message.answer("📋 Список забаненных пользователей пуст")
        return
    await message.answer("📋 Список забаненных пользователей:", reply_markup=kb_banned_list())

@router.message(Command("off"), F.chat.id == MOD_CHAT_ID)
async def cmd_off(message: Message) -> None:
    global BOT_ENABLED
    BOT_ENABLED = False
    await message.answer("✅ Бот отключен (технические работы)")
    logger.info("Бот отключен модератором")

@router.message(Command("on"), F.chat.id == MOD_CHAT_ID)
async def cmd_on(message: Message) -> None:
    global BOT_ENABLED
    BOT_ENABLED = True
    await message.answer("✅ Бот включен")
    logger.info("Бот включен модератором")

@router.message(Command("offkd"), F.chat.id == MOD_CHAT_ID)
async def cmd_offkd(message: Message) -> None:
    global COOLDOWN_ENABLED
    COOLDOWN_ENABLED = False
    await message.answer("✅ Кулдаун отключен")
    logger.info("Кулдаун отключен модератором")

@router.message(Command("onkd"), F.chat.id == MOD_CHAT_ID)
async def cmd_onkd(message: Message) -> None:
    global COOLDOWN_ENABLED
    COOLDOWN_ENABLED = True
    await message.answer("✅ Кулдаун включен")
    logger.info("Кулдаун включен модератором")

@router.message(Command("kdstatus"), F.chat.id == MOD_CHAT_ID)
async def cmd_kdstatus(message: Message) -> None:
    status = "включен" if COOLDOWN_ENABLED else "отключен"
    await message.answer(f"📊 Кулдаун: {status}")

@router.message(Command("send"), F.chat.id == MOD_CHAT_ID)
async def cmd_send(message: Message, state: FSMContext) -> None:
    lang = "ru"
    await state.set_state(ModForm.waiting_broadcast)
    await message.answer(
        TEXTS[lang]["broadcast_prompt"],
        reply_markup=kb_broadcast_cancel(lang)
    )

@router.message(Command("msg"), F.chat.id == MOD_CHAT_ID)
async def cmd_msg(message: Message) -> None:
    lang = "ru"
    text = message.text
    
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(TEXTS[lang]["msg_invalid"])
        return
    
    try:
        user_id = int(parts[1])
        msg_text = parts[2]
    except ValueError:
        await message.answer(TEXTS[lang]["msg_invalid"])
        return
    
    try:
        await send_with_photo(
            message,
            TEXTS[lang]["msg_from_mod"].format(text=msg_text),
            chat_id=user_id
        )
        await message.answer(TEXTS[lang]["msg_sent"].format(user_id=user_id))
        logger.info(f"📨 Модератор отправил сообщение пользователю {user_id}")
    except Exception as e:
        await message.answer(TEXTS[lang]["msg_fail"].format(user_id=user_id))
        logger.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")

@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    try:
        await callback.message.edit_text("Отменено")
    except Exception:
        pass

@router.message(ModForm.waiting_broadcast, F.text)
async def process_broadcast(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    
    if not ALL_USERS:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    success_count = 0
    fail_count = 0
    
    for user_id in ALL_USERS:
        try:
            await send_with_photo(
                message,
                text,
                chat_id=user_id
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1
    
    lang = "ru"
    await message.answer(TEXTS[lang]["broadcast_sent"].format(count=success_count))
    if fail_count > 0:
        await message.answer(f"⚠️ Не удалось отправить {fail_count} пользователям")
    
    logger.info(f"📨 Рассылка отправлена {success_count} пользователям, ошибок: {fail_count}")
    await state.clear()

@router.message(ModForm.waiting_broadcast)
async def process_broadcast_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "reports_list")
async def cb_reports_list(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    await edit_mod_message(
        callback.bot, callback.message.chat.id, callback.message.message_id,
        "📋 Список активных репортов:", reply_markup=kb_reports_list()
    )

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()

@router.callback_query(F.data == "reject_cancel")
async def cb_reject_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    try:
        await callback.message.edit_text("Отменено")
    except Exception:
        pass

@router.callback_query(F.data == "question_reply_cancel")
async def cb_question_reply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    try:
        await callback.message.edit_text("Отменено")
    except Exception:
        pass

@router.callback_query(F.data.startswith("view_"))
async def cb_view_report(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_", 1)[1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("Репорт не найден")
        return
    await edit_mod_message(
        callback.bot, callback.message.chat.id, callback.message.message_id,
        report_caption(rid, r), reply_markup=kb_report_detail(rid, r["user_id"], r["status"])
    )

# ============ ДЕЙСТВИЯ МОДЕРАТОРОВ ============

@router.callback_query(F.data.startswith("ban_unban_"))
async def ban_unban_user(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    user_id = int(callback.data.split("_")[-1])
    
    if user_id in BANNED_USERS:
        lang = BANNED_USERS[user_id].get("lang", "ru")
        del BANNED_USERS[user_id]
        # ОТПРАВЛЯЕМ В ЧАТ - системное уведомление о разбане
        try:
            await send_with_photo(
                callback.message,
                TEXTS[lang]["unbanned"],
                chat_id=user_id
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о разбане: {e}")
        
        if BANNED_USERS:
            await edit_mod_message(
                callback.bot, callback.message.chat.id, callback.message.message_id,
                "📋 Список забаненных пользователей:", reply_markup=kb_banned_list()
            )
        else:
            await edit_mod_message(
                callback.bot, callback.message.chat.id, callback.message.message_id,
                "📋 Список забаненных пользователей пуст"
            )
    else:
        await callback.message.answer("❌ Пользователь не в бане")

@router.callback_query(F.data.startswith("appeal_approve_"))
async def appeal_approve(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    aid = int(callback.data.split("_")[-1])
    appeal = APPEALS.get(aid)
    if not appeal:
        await callback.message.answer("❌ Апелляция не найдена")
        return
    user_id = appeal["user_id"]
    lang = appeal.get("lang", "ru")
    if user_id in BANNED_USERS:
        del BANNED_USERS[user_id]
    if aid in APPEALS:
        del APPEALS[aid]
    try:
        # ОТПРАВЛЯЕМ В ЧАТ - системное уведомление об одобрении апелляции
        try:
            await send_with_photo(
                callback.message,
                TEXTS[lang]["appeal_approved"],
                chat_id=user_id
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об одобрении апелляции: {e}")
        
        await edit_mod_message(
            callback.bot, callback.message.chat.id, callback.message.message_id,
            f"✅ Апелляция #{aid} одобрена\nПользователь {appeal['name']} разблокирован"
        )
        logger.info(f"✅ Апелляция #{aid} одобрена, пользователь {user_id} разблокирован")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("appeal_reject_"))
async def appeal_reject(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 5 секунд"), show_alert=True)
        return
    
    await callback.answer()
    aid = int(callback.data.split("_")[-1])
    appeal = APPEALS.get(aid)
    if not appeal:
        await callback.message.answer("❌ Апелляция не найдена")
        return
    user_id = appeal["user_id"]
    lang = appeal.get("lang", "ru")
    if user_id in BANNED_USERS:
        BANNED_USERS[user_id]["permanent"] = True
        BANNED_USERS[user_id]["appeal_sent"] = False
        BANNED_USERS[user_id]["permanent_notified"] = True
    if aid in APPEALS:
        del APPEALS[aid]
    try:
        # ОТПРАВЛЯЕМ В ЧАТ - системное уведомление об отклонении апелляции
        try:
            await send_with_photo(
                callback.message,
                TEXTS[lang]["appeal_rejected"],
                chat_id=user_id
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об отклонении апелляции: {e}")
        
        await edit_mod_message(
            callback.bot, callback.message.chat.id, callback.message.message_id,
            f"❌ Апелляция #{aid} отклонена\nПользователь {appeal['name']} отправлен в вечный бан"
        )
        logger.info(f"❌ Апелляция #{aid} отклонена, пользователь {user_id} в вечном бане")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("mod_approve_"))
async def mod_approve(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Репорт не найден")
        return
    try:
        r["status"] = "approved"
        r["viewed"] = False
        # НЕ ОТПРАВЛЯЕМ В ЧАТ - только через кнопки "Мои репорты"
        await edit_mod_message(
            callback.bot, callback.message.chat.id, callback.message.message_id,
            report_caption(rid, r) + "\n\n✅ Статус: ПРИНЯТ",
            reply_markup=None
        )
        logger.info(f"✅ Репорт #{rid} принят модератором")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("mod_reject_"))
async def mod_reject(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Репорт не найден")
        return
    lang = r.get("lang", "ru")
    await state.update_data(report_id=rid)
    await state.set_state(ModForm.waiting_reject_reason)
    await callback.message.answer(
        TEXTS[lang]["reject_reason_prompt"],
        reply_markup=kb_cancel_reject(lang)
    )

@router.message(ModForm.waiting_reject_reason, F.text)
async def process_reject_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    rid = data.get("report_id")
    r = REPORTS.get(rid)
    if not r:
        await message.answer("❌ Репорт не найден")
        await state.clear()
        return
    reason = message.text.strip()
    try:
        r["status"] = "rejected"
        r["reject_reason"] = reason
        r["viewed"] = False
        # НЕ ОТПРАВЛЯЕМ В ЧАТ - только через кнопки "Мои репорты"
        
        await edit_mod_message(
            message.bot, MOD_CHAT_ID, r["mod_msg_id"],
            report_caption(rid, r) + f"\n\n❌ Репорт отклонён\nПричина: {reason}",
            reply_markup=None
        )
        asyncio.create_task(_auto_delete_message(message.bot, MOD_CHAT_ID, r["mod_msg_id"], 10))
        logger.info(f"❌ Репорт #{rid} отклонён модератором. Причина: {reason}")
        await message.answer(f"✅ Репорт #{rid} отклонён.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()

@router.message(ModForm.waiting_reject_reason)
async def process_reject_reason_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("mod_ban_"))
async def mod_ban(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Репорт не найден")
        return
    user_id = r["user_id"]
    lang = r.get("lang", "ru")
    BANNED_USERS[user_id] = {
        "reason": "Забанен модератором",
        "permanent": False,
        "appeal_sent": False,
        "name": r["full_name"],
        "lang": lang,
    }
    # ОТПРАВЛЯЕМ В ЧАТ - системное уведомление о бане
    try:
        await send_with_photo(
            callback.message,
            TEXTS[lang]["banned"],
            reply_markup=kb_appeal(lang),
            chat_id=user_id
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о бане: {e}")
    
    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"], r["status"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

@router.callback_query(F.data.startswith("mod_unban_"))
async def mod_unban(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Репорт не найден")
        return
    user_id = r["user_id"]
    lang = r.get("lang", "ru")
    if user_id in BANNED_USERS:
        del BANNED_USERS[user_id]
        # ОТПРАВЛЯЕМ В ЧАТ - системное уведомление о разбане
        try:
            await send_with_photo(
                callback.message,
                TEXTS[lang]["unbanned"],
                chat_id=user_id
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о разбане: {e}")
    
    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"], r["status"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

@router.callback_query(F.data.startswith("mod_blocked_"))
async def mod_blocked(callback: CallbackQuery) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Репорт не найден")
        return
    lang = r.get("lang", "ru")
    target_type = detect_target_type(r["link"])
    type_noun = TARGET_TYPE_NOUN.get(lang, TARGET_TYPE_NOUN["ru"]).get(target_type, "материал")
    try:
        r["status"] = "blocked"
        r["viewed"] = False
        
        # НЕ ОТПРАВЛЯЕМ В ЧАТ - только через кнопки "Мои репорты"
        
        await edit_mod_message(
            callback.bot, callback.message.chat.id, callback.message.message_id,
            report_caption(rid, r) + f"\n\n🚫 {type_noun.capitalize()} заблокирован",
            reply_markup=kb_report_detail(rid, r["user_id"], r["status"])
        )
        await callback.message.answer(TEXTS[lang]["blocked_sent"])
        logger.info(f"🚫 По репорту #{rid} подтверждена блокировка ({type_noun})")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("mod_question_reply_"))
async def mod_question_reply(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer(TEXTS.get("ru", {}).get("button_cooldown", "⏳ Подождите 3 секунды"), show_alert=True)
        return
    
    await callback.answer()
    qid = int(callback.data.split("_")[-1])
    q = QUESTIONS.get(qid)
    if not q:
        await callback.message.answer("❌ Вопрос не найден")
        return
    lang = q.get("lang", "ru")
    await state.update_data(question_id=qid)
    await state.set_state(ModForm.waiting_question_reply)
    await callback.message.answer(
        TEXTS[lang]["question_reply_prompt"].format(id=qid),
        reply_markup=kb_cancel_question_reply(lang)
    )

@router.message(ModForm.waiting_question_reply, F.text)
async def process_question_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    qid = data.get("question_id")
    q = QUESTIONS.get(qid)
    if not q:
        await message.answer("❌ Вопрос не найден")
        await state.clear()
        return
    lang = q.get("lang", "ru")
    answer = message.text.strip()
    try:
        q["answered"] = True
        q["answer"] = answer
        q["viewed"] = False
        
        # НЕ ОТПРАВЛЯЕМ В ЧАТ - только через кнопки "Мои вопросы"
        
        await message.answer(f"✅ Ответ на вопрос #{qid} сохранён. Пользователь увидит в 'Моих вопросах'")
        logger.info(f"❓ Ответ на вопрос #{qid} сохранён для пользователя {q['user_id']}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()

@router.message(ModForm.waiting_question_reply)
async def process_question_reply_invalid(message: Message) -> None:
 try:
   await message.delete()
 except Exception:
   pass

# ============ БЛОКИРОВКА (ЖАЛОБЫ В TELEGRAM ЧЕРЕЗ АККАУНТЫ) ============

@router.message(Command("block"), F.chat.id == MOD_CHAT_ID)
async def cmd_block(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()
    sent = await send_with_photo(
        message,
        f"🔒 Меню отправки жалоб в Telegram\n\nАккаунтов добавлено: {len(BOTREPORT_SESSIONS)}",
        reply_markup=kb_block_menu()
    )
    if sent:
        await state.update_data(screen_msg_id=sent.message_id)

@router.callback_query(F.data == "block_back_to_menu")
async def block_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    screen_msg_id = (await state.get_data()).get("screen_msg_id")
    await state.clear()
    await state.update_data(screen_msg_id=screen_msg_id)
    await render_screen(
        callback.message, state,
        f"🔒 Меню отправки жалоб в Telegram\n\nАккаунтов добавлено: {len(BOTREPORT_SESSIONS)}",
        kb_block_menu()
    )

@router.callback_query(F.data == "block_cancel")
async def block_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    screen_msg_id = (await state.get_data()).get("screen_msg_id")
    await state.clear()
    await state.update_data(screen_msg_id=screen_msg_id)
    await render_screen(
        callback.message, state,
        f"🔒 Меню отправки жалоб в Telegram\n\nАккаунтов добавлено: {len(BOTREPORT_SESSIONS)}",
        kb_block_menu()
    )

@router.callback_query(F.data == "noop")
async def block_noop(callback: CallbackQuery) -> None:
    await callback.answer()

# ---- Добавление сессий ----

@router.callback_query(F.data == "block_add_sessions")
async def block_add_sessions(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    await render_screen(
        callback.message, state,
        "📥 Пришлите строки сессий (session string) — можно несколько сразу, "
        "каждую с новой строки.\n\nКаждой будет присвоено имя session1, session2 и т.д.",
        kb_block_cancel()
    )
    await state.set_state(BlockForm.waiting_session_strings)

@router.message(BlockForm.waiting_session_strings, F.text)
async def process_session_strings(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer(TEXTS["ru"]["bot_disabled"])
        return
    if not API_ID or not API_HASH:
        await message.answer("❌ API_ID / API_HASH не настроены на сервере — добавление аккаунтов недоступно.")
        return
    lines = [ln.strip() for ln in message.text.splitlines() if ln.strip()]
    try:
        await message.delete()
    except Exception:
        pass
    if not lines:
        return
    added, failed = [], []
    for session_string in lines:
        try:
            client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
            await asyncio.wait_for(client.connect(), timeout=20)
            if not await client.is_user_authorized():
                await client.disconnect()
                failed.append("не авторизована")
                continue
            client.flood_sleep_threshold = 0
            name = next_session_name()
            BOTREPORT_SESSIONS[name] = client
            raw = load_sessions_from_disk()
            raw[name] = session_string
            save_sessions_to_disk(raw)
            added.append(name)
        except Exception as e:
            logger.error(f"Не удалось добавить сессию: {e}")
            failed.append(str(e)[:60])

    result_text = f"✅ Добавлено аккаунтов: {len(added)}\n"
    if added:
        result_text += "  " + ", ".join(added) + "\n"
    if failed:
        result_text += f"❌ Не удалось добавить: {len(failed)}\n  " + "; ".join(failed)

    screen_msg_id = (await state.get_data()).get("screen_msg_id")
    await state.clear()
    await state.update_data(screen_msg_id=screen_msg_id)
    await render_screen(message, state, result_text, kb_block_back_to_menu())

# ---- Список / удаление аккаунтов ----

@router.callback_query(F.data == "block_list_sessions")
async def block_list_sessions(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    if not BOTREPORT_SESSIONS:
        text = "📋 Аккаунтов пока нет.\n\nДобавьте их через «➕ Добавить сессии»."
    else:
        text = f"📋 Аккаунты для жалоб ({len(BOTREPORT_SESSIONS)}):\n\nНажмите на аккаунт, чтобы удалить его."
    await render_screen(callback.message, state, text, kb_block_sessions_list())

@router.callback_query(F.data.startswith("block_del_session_"))
async def block_del_session(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    name = callback.data[len("block_del_session_"):]
    client = BOTREPORT_SESSIONS.pop(name, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
        raw = load_sessions_from_disk()
        raw.pop(name, None)
        save_sessions_to_disk(raw)
        await callback.answer(f"🗑 {name} удалён")
    else:
        await callback.answer("Уже удалён")
    text = f"📋 Аккаунты для жалоб ({len(BOTREPORT_SESSIONS)}):\n\nНажмите на аккаунт, чтобы удалить его." if BOTREPORT_SESSIONS else "📋 Аккаунтов пока нет."
    await render_screen(callback.message, state, text, kb_block_sessions_list())

# ---- Отправка жалоб ----

@router.callback_query(F.data == "block_send_reports")
async def block_send_reports(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    if not BOTREPORT_SESSIONS:
        await render_screen(
            callback.message, state,
            "❌ Нет ни одного добавленного аккаунта.\n\nСначала добавьте сессии через «➕ Добавить сессии».",
            kb_block_back_to_menu()
        )
        return
    await render_screen(
        callback.message, state,
        f"👥 Доступно аккаунтов: {len(BOTREPORT_SESSIONS)}\n\n"
        f"✏️ Пришлите юзернейм бота, на которого нужно пожаловаться (например @somebot):",
        kb_block_cancel()
    )
    await state.set_state(BlockForm.waiting_report_username)

@router.message(BlockForm.waiting_report_username, F.text)
async def process_report_username(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer(TEXTS["ru"]["bot_disabled"])
        return
    username = message.text.strip()
    if not username.startswith("@"):
        username = f"@{username}"
    try:
        await message.delete()
    except Exception:
        pass
    await state.update_data(bot_username=username)
    screen_msg_id = (await state.get_data()).get("screen_msg_id")
    await send_with_photo(
        message,
        f"🎯 Бот: {username}\n\nВыберите причину жалобы:",
        reply_markup=kb_report_reasons(),
        edit_msg_id=screen_msg_id,
        chat_id=message.chat.id
    )

@router.callback_query(F.data.startswith("block_reason_"))
async def block_pick_reason(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    reason_key = callback.data[len("block_reason_"):]
    if reason_key not in REASON_MAP:
        return
    await state.update_data(reason_key=reason_key)
    data = await state.get_data()
    bot_username = data.get("bot_username", "?")
    await render_screen(
        callback.message, state,
        f"🎯 Бот: {bot_username}\n"
        f"📝 Причина: {REASON_DISPLAY.get(reason_key)}\n\n"
        f"Сколько аккаунтов использовать для отправки жалоб?\n"
        f"Доступно: {len(BOTREPORT_SESSIONS)}",
        kb_session_count()
    )

@router.callback_query(F.data.startswith("block_count_"))
async def block_pick_count(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    raw = callback.data[len("block_count_"):]
    available = len(BOTREPORT_SESSIONS)
    session_count = available if raw == "all" else max(1, min(int(raw), available))
    await state.update_data(session_count=session_count)
    data = await state.get_data()
    bot_username = data.get("bot_username", "?")
    reason_key = data.get("reason_key")
    await render_screen(
        callback.message, state,
        f"🎯 Бот: {bot_username}\n"
        f"📝 Причина: {REASON_DISPLAY.get(reason_key)}\n"
        f"👥 Аккаунтов будет использовано: {session_count} из {available}\n\n"
        f"Отправить со стандартным текстом жалобы, или ввести свой?",
        kb_report_confirm()
    )

@router.callback_query(F.data == "block_custom_text")
async def block_custom_text(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    await render_screen(
        callback.message, state,
        "✏️ Пришлите текст жалобы:",
        kb_block_cancel()
    )
    await state.set_state(BlockForm.waiting_custom_text)

@router.message(BlockForm.waiting_custom_text, F.text)
async def process_custom_text(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer(TEXTS["ru"]["bot_disabled"])
        return
    custom_text = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    bot_username = data.get("bot_username")
    reason_key = data.get("reason_key")
    session_count = data.get("session_count")
    await state.clear()
    asyncio.create_task(
        run_report_sending(message.bot, message.chat.id, bot_username, reason_key, custom_text, session_count)
    )

@router.callback_query(F.data == "block_start_sending")
async def block_start_sending(callback: CallbackQuery, state: FSMContext) -> None:
    if not check_button_cooldown(callback.from_user.id):
        await callback.answer("⏳ Подождите 2 секунды", show_alert=True)
        return
    await callback.answer()
    data = await state.get_data()
    bot_username = data.get("bot_username")
    reason_key = data.get("reason_key")
    session_count = data.get("session_count")
    default_text = REASON_MAP.get(reason_key, (None, ""))[1]
    await state.clear()
    asyncio.create_task(
        run_report_sending(callback.bot, callback.message.chat.id, bot_username, reason_key, default_text, session_count)
    )

@router.callback_query(F.data == "block_stop_sending")
async def block_stop_sending(callback: CallbackQuery) -> None:
    task_id = str(callback.message.chat.id)
    stop_flags[task_id] = True
    await callback.answer("🛑 Останавливаю после текущей жалобы...")

async def run_report_sending(bot: Bot, chat_id: int, bot_username: str, reason_key: str, comment_text: str, session_count: int = None) -> None:
    task_id = str(chat_id)
    stop_flags[task_id] = False
    active_tasks[task_id] = asyncio.current_task()
    status_msg = None
    try:
        status_msg = await bot.send_message(
            chat_id,
            f"🚀 Начинаю отправку жалоб на {bot_username}\n[{CODE_VERSION}]\nИщу бота..."
        )
        logger.info(f"[{CODE_VERSION}] Старт отправки жалоб на {bot_username}, всего сессий: {len(BOTREPORT_SESSIONS)}, запрошено: {session_count}")

        all_sessions = sorted(BOTREPORT_SESSIONS.items(), key=lambda x: x[0])
        if not all_sessions:
            await status_msg.edit_text("❌ Нет добавленных аккаунтов.")
            return
        total_available = len(all_sessions)
        n = session_count if session_count else total_available
        n = max(1, min(n, total_available))
        sessions = all_sessions[:n]

        selected_reason, _ = REASON_MAP.get(reason_key, (None, ""))
        if selected_reason is None:
            await status_msg.edit_text("❌ Некорректная причина жалобы.")
            return

        first_client = sessions[0][1]
        first_client.flood_sleep_threshold = 0
        bot_entity = await asyncio.wait_for(first_client.get_entity(bot_username), timeout=20)
        logger.info(f"Бот найден: id={bot_entity.id} username={getattr(bot_entity, 'username', None)}")

        bot_display_name = " ".join(filter(None, [
            getattr(bot_entity, "first_name", None),
            getattr(bot_entity, "last_name", None)
        ])) or "—"
        bot_info_line = (
            f"🤖 Бот: {bot_display_name} | @{getattr(bot_entity, 'username', None) or bot_username.lstrip('@')} | ID: {bot_entity.id}\n"
            f"👥 Аккаунтов используется: {n} из {total_available}"
        )

        text_variations = await generate_variations(comment_text)
        reports_per_type = random.randint(3, 6)
        total_reports = len(sessions) * reports_per_type * 2
        current_reports = 0
        success_count = 0
        error_count = 0

        async def update_status(extra: str = ""):
            txt = (
                f"{bot_info_line}\n"
                f"📝 Причина: {REASON_DISPLAY.get(reason_key, 'Неизвестно')}\n\n"
                f"{render_progressbar(current_reports, total_reports)}\n"
                f"📨 Отправлено: {current_reports}/{total_reports}\n"
                f"✅ Успешно: {success_count}   ❌ Ошибок: {error_count}"
                + (f"\n{extra}" if extra else "")
            )
            try:
                await status_msg.edit_text(txt, reply_markup=kb_report_stop())
            except Exception as e:
                logger.warning(f"⚠️ Не удалось обновить статус: {e}")

        await update_status("⏳ Начинаю...")

        for session_name, session_client in sessions:
            session_client.flood_sleep_threshold = 0
            if stop_flags.get(task_id, False):
                await update_status("🛑 Остановлено")
                return
            await update_status(f"🔗 Сессия {session_name}: получаю сообщения бота...")
            try:
                bot_messages = await asyncio.wait_for(
                    session_client.get_messages(bot_entity, limit=1), timeout=20
                )
                logger.info(f"[{session_name}] Получено сообщений бота: {len(bot_messages) if bot_messages else 0}")

                if bot_messages:
                    last_msg = bot_messages[0]
                    for i in range(reports_per_type):
                        if stop_flags.get(task_id, False):
                            break
                        text_variant = random.choice(text_variations)
                        res = await send_message_report(
                            session_client, bot_entity, last_msg.id,
                            selected_reason, text_variant
                        )
                        logger.info(f"[{session_name}] Жалоба на сообщение #{i+1}: {'OK' if res else 'FAIL'}")
                        if res:
                            success_count += 1
                        else:
                            error_count += 1
                        current_reports += 1
                        await update_status()
                        await asyncio.sleep(random.uniform(2.5, 4.5))

                for i in range(reports_per_type):
                    if stop_flags.get(task_id, False):
                        break
                    text_variant = random.choice(text_variations)
                    res = await send_peer_report(
                        session_client, bot_entity,
                        selected_reason, text_variant
                    )
                    logger.info(f"[{session_name}] Жалоба на профиль #{i+1}: {'OK' if res else 'FAIL'}")
                    if res:
                        success_count += 1
                    else:
                        error_count += 1
                    current_reports += 1
                    await update_status()
                    await asyncio.sleep(random.uniform(2.5, 4.5))
            except Exception as e:
                logger.error(f"Ошибка сессии {session_name}: {e}")
                error_count += 1

        try:
            await status_msg.edit_text(
                f"{bot_info_line}\n"
                f"📝 Причина: {REASON_DISPLAY.get(reason_key, 'Неизвестно')}\n\n"
                f"✨ Готово!\n"
                f"{render_progressbar(current_reports, total_reports)}\n"
                f"📨 Отправлено: {current_reports}/{total_reports}\n"
                f"✅ Успешно: {success_count}   ❌ Ошибок: {error_count}"
            )
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить финальный статус: {e}")
    except asyncio.TimeoutError:
        logger.error("Таймаут: бот не отвечает (20с)")
        err_text = f"❌ Ошибка\n\nБот {bot_username} не отвечает (таймаут 20с). Юзернейм неверный или Telegram сейчас медленно отвечает."
        if status_msg:
            await status_msg.edit_text(err_text)
        else:
            await bot.send_message(chat_id, err_text)
    except Exception as e:
        logger.error(f"Ошибка отправки жалоб: {e}")
        if status_msg:
            await status_msg.edit_text(f"❌ Ошибка\n\n{e}")
        else:
            await bot.send_message(chat_id, f"❌ Ошибка\n\n{e}")
    finally:
        if task_id in active_tasks:
            del active_tasks[task_id]
        if task_id in stop_flags:
            del stop_flags[task_id]

   # ============ WEBHOOK ============

async def webhook_handler(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        update = Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.Response(status=500)

async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "bot": "running"})

async def keep_alive_loop() -> None:
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        logger.info("ℹ️ RENDER_EXTERNAL_HOSTNAME не задан — keep-alive пинг отключён")
        return
    url = f"https://{hostname}/health"
    await asyncio.sleep(10)
    async with aiohttp.ClientSession() as session:
        while True:
            success = False
            for attempt in range(3):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        logger.info(f"🔄 Keep-alive пинг: {resp.status}")
                        success = True
                        break
                except Exception as e:
                    logger.warning(f"⚠️ Keep-alive пинг не удался (попытка {attempt + 1}/3): {e}")
                    await asyncio.sleep(5)
            if not success:
                logger.error("❌ Keep-alive: все попытки пинга провалились в этом цикле")
            await asyncio.sleep(150)

async def on_startup(app: web.Application) -> None:
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}{WEBHOOK_PATH}"
    if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
        me = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{me.username}")
    except Exception as e:
        logger.error(f"❌ Ошибка при старте: {e}")
    if API_ID and API_HASH:
        try:
            await load_sessions_on_startup()
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки сессий для жалоб: {e}")
    app["keep_alive_task"] = asyncio.create_task(keep_alive_loop())

async def on_shutdown(app: web.Application) -> None:
    task = app.get("keep_alive_task")
    if task:
        task.cancel()
    try:
        await bot.delete_webhook()
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.error(f"❌ Ошибка удаления webhook: {e}")
    for name, client in list(BOTREPORT_SESSIONS.items()):
        try:
            await client.disconnect()
        except Exception:
            pass

# ============ ЗАПУСК ============

async def main() -> None:
    global bot, dp
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    logger.info(f"🚀 Сервер запущен на порту {WEB_SERVER_PORT}")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("⏹️ Остановка...")
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
