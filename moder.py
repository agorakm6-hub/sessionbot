"""
Telegram-бот для приёма жалоб с модерацией
Работает на webhook через порт 10000 для Render.com
"""

import asyncio
import logging
import math
import os
import sys
from datetime import datetime

import aiohttp
from aiohttp import web
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
    Update,
    FSInputFile,
)

# ============ НАСТРОЙКИ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не установлен!")
    sys.exit(1)

WEBHOOK_PATH = "/webhook"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "10000"))

# ID чата модераторов
MOD_CHAT_ID = -1004354663980

# Кулдаун между жалобами (в минутах)
COOLDOWN_MINUTES = 30
COOLDOWN_SECONDS = COOLDOWN_MINUTES * 60

# Путь к аватарке бота - JPG
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
    # ============ ТЕКСТЫ ============

TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык:",
        "confirm_bot": "Подтвердите, что вы не робот:",
        "confirm_bot_btn": "Я не робот",
        "main_menu": "Главное меню:\nВыберите действие:",
        "report_btn": "📝 Репорт",
        "question_btn": "❓ Общение",
        "enter_link": "Введите ссылку на нарушающий материал:",
        "enter_reason": "Опишите суть жалобы:",
        "sent": "Ваша жалоба отправлена на рассмотрение.",
        "back_btn": "⬅️ Вернуться",
        "approved": "Ваша жалоба принята",
        "rejected": "Ваша жалоба отклонена",
        "rejected_with_msg": "Ваша жалоба отклонена.\n\nПричина: {msg}",
        "cooldown": "⏳ Действует кулдаун. Подождите {minutes} мин.",
        "banned": "🚫 Вы заблокированы!\n\nХотите подать апелляцию?",
        "banned_permanent": "🚫 Ваша апелляция отклонена. Вы больше не можете пользоваться ботом.",
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
        "blocked": (
            "Здравствуйте! Вы отправили жалобу на:\n{link}\n\n"
            "После тщательного расследования мы пришли к выводу, что {type} "
            "действительно нарушает правила использования (ToS) и был заблокирован.\n\n"
            "Спасибо, что помогаете бороться с незаконным контентом!"
        ),
        "reject_reason_prompt": "✏️ Напишите причину отказа для пользователя:",
        "reject_reason_cancel": "Отмена",
        "question_prompt": "Введите ваш вопрос:",
        "question_sent": "Ваш вопрос отправлен. Ждите ответа.",
        "question_notification": "❓ Новый вопрос\n\nОт: {name} (@{username})\nID: {user_id}\n\nВопрос: {text}",
        "question_reply_prompt": "✏️ Напишите ответ на вопрос:",
        "question_reply_format": "Ответ на вопрос: {answer}",
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
            "/reports - Список активных жалоб\n"
            "/banned - Список забаненных пользователей\n"
            "/off - Отключить бота (технические работы)\n"
            "/on - Включить бота\n"
            "/offkd - Отключить кулдаун\n"
            "/onkd - Включить кулдаун\n"
            "/kdstatus - Статус кулдауна\n"
            "/send - Сделать рассылку всем пользователям\n"
            "/help - Показать это сообщение"
        ),
    },
    "ua": {
        "choose_lang": "Оберіть мову:",
        "confirm_bot": "Підтвердіть, що ви не робот:",
        "confirm_bot_btn": "Я не робот",
        "main_menu": "Головне меню:\nВиберіть дію:",
        "report_btn": "📝 Репорт",
        "question_btn": "❓ Спілкування",
        "enter_link": "Введіть посилання на матеріал, що порушує правила:",
        "enter_reason": "Опишіть суть скарги:",
        "sent": "Вашу скаргу надіслано на розгляд.",
        "back_btn": "⬅️ Повернутися",
        "approved": "Вашу скаргу прийнято",
        "rejected": "Вашу скаргу відхилено",
        "rejected_with_msg": "Вашу скаргу відхилено.\n\nПричина: {msg}",
        "cooldown": "⏳ Діє кулдаун. Зачекайте {minutes} хв.",
        "banned": "🚫 Вас заблоковано!\n\nХочете подати апеляцію?",
        "banned_permanent": "🚫 Вашу апеляцію відхилено. Ви більше не можете користуватися ботом.",
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
        "blocked": (
            "Вітаємо! Ви надіслали скаргу на:\n{link}\n\n"
            "Після ретельного розслідування ми дійшли висновку, що {type} "
            "справді порушує правила використання (ToS) і було заблоковано.\n\n"
            "Дякуємо, що допомагаєте боротися з незаконним контентом!"
        ),
        "reject_reason_prompt": "✏️ Напишіть причину відмови для користувача:",
        "reject_reason_cancel": "Скасування",
        "question_prompt": "Введіть ваше питання:",
        "question_sent": "Ваше питання надіслано. Чекайте відповіді.",
        "question_notification": "❓ Нове питання\n\nВід: {name} (@{username})\nID: {user_id}\n\nПитання: {text}",
        "question_reply_prompt": "✏️ Напишіть відповідь на питання:",
        "question_reply_format": "Відповідь на питання: {answer}",
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
            "/reports - Список активних скарг\n"
            "/banned - Список заблокованих користувачів\n"
            "/off - Вимкнути бота (технічні роботи)\n"
            "/on - Увімкнути бота\n"
            "/offkd - Вимкнути кулдаун\n"
            "/onkd - Увімкнути кулдаун\n"
            "/kdstatus - Статус кулдауну\n"
            "/send - Зробити розсилку всім користувачам\n"
            "/help - Показати це повідомлення"
        ),
    },
    "en": {
        "choose_lang": "Choose language:",
        "confirm_bot": "Please confirm you're not a robot:",
        "confirm_bot_btn": "I'm not a robot",
        "main_menu": "Main menu:\nChoose an action:",
        "report_btn": "📝 Report",
        "question_btn": "❓ Contact",
        "enter_link": "Enter the link to the violating content:",
        "enter_reason": "Describe the violation:",
        "sent": "Your report has been sent for review.",
        "back_btn": "⬅️ Back",
        "approved": "Your report has been approved",
        "rejected": "Your report has been rejected",
        "rejected_with_msg": "Your report has been rejected.\n\nReason: {msg}",
        "cooldown": "⏳ Cooldown active. Please wait {minutes} min.",
        "banned": "🚫 You are banned!\n\nDo you want to appeal?",
        "banned_permanent": "🚫 Your appeal has been rejected. You can no longer use the bot.",
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
        "blocked": (
            "Hello! You submitted a report on:\n{link}\n\n"
            "After a thorough investigation we concluded that the reported {type} "
            "does indeed violate the Terms of Service and has been blocked.\n\n"
            "Thank you for helping fight illegal content!"
        ),
        "reject_reason_prompt": "✏️ Please enter the rejection reason for the user:",
        "reject_reason_cancel": "Cancel",
        "question_prompt": "Enter your question:",
        "question_sent": "Your question has been sent. Waiting for response.",
        "question_notification": "❓ New question\n\nFrom: {name} (@{username})\nID: {user_id}\n\nQuestion: {text}",
        "question_reply_prompt": "✏️ Please enter your answer to the question:",
        "question_reply_format": "Answer to your question: {answer}",
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
            "/help - Show this message"
        ),
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
            [
                InlineKeyboardButton(text=TEXTS[lang]["report_btn"], callback_data="menu_report"),
                InlineKeyboardButton(text=TEXTS[lang]["question_btn"], callback_data="menu_question"),
            ]
        ]
    )

def kb_back_to_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["back_btn"], callback_data="menu_back")]
        ]
    )

def kb_appeal(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TEXTS[lang]["appeal_btn_yes"], callback_data="appeal_yes"),
                InlineKeyboardButton(text=TEXTS[lang]["appeal_btn_no"], callback_data="appeal_no"),
            ]
        ]
    )

def kb_moderator_actions(report_id: int, user_id: int) -> InlineKeyboardMarkup:
    banned = user_id in BANNED_USERS
    ban_btn = (
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"mod_unban_{report_id}")
        if banned
        else InlineKeyboardButton(text="🚫 Забанить", callback_data=f"mod_ban_{report_id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"mod_approve_{report_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_reject_{report_id}"),
            ],
            [ban_btn],
        ]
    )

def kb_reports_list() -> InlineKeyboardMarkup:
    rows = []
    for rid, r in sorted(REPORTS.items(), key=lambda x: -x[0]):
        if r["status"] != "pending":
            continue
        label = f"#{rid} — {r['username'] or r['full_name']}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"view_{rid}")])
    if not rows:
        rows = [[InlineKeyboardButton(text="Нет активных жалоб", callback_data="noop")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_report_detail(report_id: int, user_id: int) -> InlineKeyboardMarkup:
    banned = user_id in BANNED_USERS
    ban_btn = (
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"mod_unban_{report_id}")
        if banned
        else InlineKeyboardButton(text="🚫 Забанить", callback_data=f"mod_ban_{report_id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"mod_approve_{report_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_reject_{report_id}"),
            ],
            [InlineKeyboardButton(text="🚫 Канал/бот заблокирован", callback_data=f"mod_blocked_{report_id}")],
            [ban_btn],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="reports_list")],
        ]
    )

def kb_cancel_reject(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["reject_reason_cancel"], callback_data="reject_cancel")]
        ]
    )

def kb_moderator_question_actions(qid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"mod_question_reply_{qid}")]
        ]
    )

def kb_cancel_question_reply(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["reject_reason_cancel"], callback_data="question_reply_cancel")]
        ]
    )

def kb_appeal_actions(appeal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"appeal_approve_{appeal_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"appeal_reject_{appeal_id}"),
            ]
        ]
    )

def kb_banned_list() -> InlineKeyboardMarkup:
    rows = []
    for user_id, data in BANNED_USERS.items():
        label = f"{data.get('name', 'Unknown')} (ID: {user_id})"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"ban_unban_{user_id}")])
    if not rows:
        rows = [[InlineKeyboardButton(text="Список пуст", callback_data="noop")]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_broadcast_cancel(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=TEXTS[lang]["reject_reason_cancel"], callback_data="broadcast_cancel")]
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
        f"🚨 Новая жалоба #{rid}\n\n"
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

async def show_main_menu(message: Message, state: FSMContext, edit: bool = False) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if edit and data.get("menu_msg_id"):
        try:
            await message.bot.edit_message_text(
                TEXTS[lang]["main_menu"],
                chat_id=message.chat.id,
                message_id=data["menu_msg_id"],
                reply_markup=kb_main_menu(lang)
            )
            return
        except Exception:
            pass
    
    sent = await message.answer(TEXTS[lang]["main_menu"], reply_markup=kb_main_menu(lang))
    await state.update_data(menu_msg_id=sent.message_id)

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    
    # Добавляем пользователя в список всех пользователей
    ALL_USERS.add(user_id)

    await cleanup_tracked(message.bot, message.chat.id, state)
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()

    # Проверка на отключенный бот
    if not BOT_ENABLED:
        lang = "ru"
        await message.answer(TEXTS[lang]["bot_disabled"])
        return

    prefs = USER_PREFS.get(user_id)
    lang = prefs["lang"] if prefs else "ru"
    await state.update_data(lang=lang)

    # Проверка на вечный бан
    if user_id in BANNED_USERS and BANNED_USERS[user_id].get("permanent", False):
        await message.answer(TEXTS[lang]["banned_permanent"])
        return

    if user_id in BANNED_USERS:
        # Пользователь в бане, предлагаем апелляцию
        sent = await message.answer(TEXTS[lang]["banned"], reply_markup=kb_appeal(lang))
        await track(state, sent.message_id)
        return

    if prefs and prefs.get("confirmed"):
        await show_main_menu(message, state)
        logger.info(f"👤 Пользователь {user_id} запустил бота (повтор, lang={lang})")
        return

    # Отправляем с аватаркой при первом запуске
    if BOT_AVATAR and os.path.exists(BOT_AVATAR):
        try:
            photo = FSInputFile(BOT_AVATAR)
            sent = await message.answer_photo(
                photo=photo,
                caption="Выберите язык / Оберіть мову / Choose language:",
                reply_markup=kb_language()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            sent = await message.answer(
                "Выберите язык / Оберіть мову / Choose language:",
                reply_markup=kb_language()
            )
    else:
        sent = await message.answer(
            "Выберите язык / Оберіть мову / Choose language:",
            reply_markup=kb_language()
        )
    
    await track(state, sent.message_id)
    await state.update_data(msg_id=sent.message_id)
    logger.info(f"👤 Пользователь {user_id} запустил бота")

@router.callback_query(F.data.startswith("lang_"))
async def process_lang(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    lang = callback.data.split("_", 1)[1]
    await state.update_data(lang=lang)
    
    # Удаляем старое сообщение
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    # Отправляем новое с аватаркой и кнопкой "Я не робот"
    if BOT_AVATAR and os.path.exists(BOT_AVATAR):
        try:
            photo = FSInputFile(BOT_AVATAR)
            await callback.message.answer_photo(
                photo=photo,
                caption=TEXTS[lang]["confirm_bot"],
                reply_markup=kb_confirm_human(lang)
            )
        except Exception as e:
            logger.error(f"Ошибка отправки фото: {e}")
            await callback.message.answer(
                TEXTS[lang]["confirm_bot"],
                reply_markup=kb_confirm_human(lang)
            )
    else:
        await callback.message.answer(
            TEXTS[lang]["confirm_bot"],
            reply_markup=kb_confirm_human(lang)
        )

@router.callback_query(F.data == "confirm_human")
async def process_confirm_human(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")

    USER_PREFS[callback.from_user.id] = {"lang": lang, "confirmed": True}
    await state.update_data(lang=lang)
    
    # Удаляем сообщение с подтверждением
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await show_main_menu(callback.message, state)

@router.callback_query(F.data == "appeal_yes")
async def appeal_yes(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    user_id = callback.from_user.id
    
    if user_id not in BANNED_USERS:
        await callback.message.answer("Вы не в бане")
        return
    
    if BANNED_USERS[user_id].get("permanent", False):
        await callback.message.answer("Вы в вечном бане, апелляция невозможна")
        return
    
    if BANNED_USERS[user_id].get("appeal_sent", False):
        lang = BANNED_USERS[user_id].get("lang", "ru")
        await callback.message.answer(TEXTS[lang]["appeal_already_sent"])
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    sent = await callback.message.answer(
        TEXTS[lang]["appeal_prompt"],
        reply_markup=kb_back_to_menu(lang)
    )
    await state.update_data(msg_id=sent.message_id)
    await state.set_state(AppealForm.waiting_appeal_text)

@router.callback_query(F.data == "appeal_no")
async def appeal_no(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await show_main_menu(callback.message, state)

@router.message(AppealForm.waiting_appeal_text, F.text)
async def process_appeal(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
    appeal_text = message.text.strip()
    
    try:
        await message.delete()
    except Exception:
        pass
    
    user = message.from_user
    
    if user.id not in BANNED_USERS:
        await message.answer("❌ Вы не в бане")
        await state.clear()
        return
    
    # Сохраняем апелляцию
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
    
    await message.bot.edit_message_text(
        TEXTS[lang]["appeal_sent"],
        chat_id=message.chat.id,
        message_id=msg_id,
        reply_markup=kb_back_to_menu(lang)
    )
    
    # Отправляем уведомление модераторам
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
    await state.update_data(lang=lang)

@router.message(AppealForm.waiting_appeal_text)
async def process_appeal_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "menu_back")
async def menu_back(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    await state.clear()
    await state.update_data(lang=lang)
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    await show_main_menu(callback.message, state)

@router.callback_query(F.data == "menu_report")
async def menu_report(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    user_id = callback.from_user.id
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if user_id in BANNED_USERS:
        await callback.message.answer(TEXTS[lang]["banned"])
        return
    
    remaining = get_cooldown_remaining_minutes(user_id)
    if remaining > 0:
        await callback.message.answer(TEXTS[lang]["cooldown"].format(minutes=remaining))
        return
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    sent = await callback.message.answer(TEXTS[lang]["enter_link"], reply_markup=kb_back_to_menu(lang))
    await state.update_data(msg_id=sent.message_id)
    await state.set_state(ReportForm.link)

@router.callback_query(F.data == "menu_question")
async def menu_question(callback: CallbackQuery, state: FSMContext) -> None:
    # ВАЖНО: сначала отвечаем на callback
    await callback.answer()
    
    if not BOT_ENABLED:
        await callback.message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    if callback.from_user.id in BANNED_USERS:
        await callback.message.answer(TEXTS[lang]["banned"])
        return
    
    try:
        await callback.message.delete()
    except Exception:
        pass
    
    sent = await callback.message.answer(TEXTS[lang]["question_prompt"], reply_markup=kb_back_to_menu(lang))
    await state.update_data(msg_id=sent.message_id)
    await state.set_state(QuestionForm.question)

@router.message(ReportForm.link, F.text)
async def process_link(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")

    await state.update_data(link=message.text.strip())

    try:
        await message.delete()
    except Exception:
        pass

    await message.bot.edit_message_text(
        TEXTS[lang]["enter_reason"],
        chat_id=message.chat.id,
        message_id=msg_id
    )
    await state.set_state(ReportForm.reason)

@router.message(ReportForm.link)
async def process_link_invalid(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.message(ReportForm.reason, F.text)
async def process_reason(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
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
    }

    await message.bot.edit_message_text(
        TEXTS[lang]["sent"],
        chat_id=message.chat.id,
        message_id=msg_id,
        reply_markup=kb_back_to_menu(lang)
    )
    await state.clear()
    await state.update_data(lang=lang)

    LAST_REPORT_TIME[user.id] = datetime.utcnow()

    try:
        sent_mod = await message.bot.send_message(
            MOD_CHAT_ID,
            report_caption(rid, REPORTS[rid]),
            reply_markup=kb_moderator_actions(rid, user.id)
        )
        REPORTS[rid]["mod_msg_id"] = sent_mod.message_id
        logger.info(f"✅ Жалоба #{rid} отправлена модераторам")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки модераторам: {e}")

@router.message(ReportForm.reason)
async def process_reason_invalid(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.message(QuestionForm.question, F.text)
async def process_question(message: Message, state: FSMContext) -> None:
    if not BOT_ENABLED:
        await message.answer("Бот отключен")
        return
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
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
    }

    await message.bot.edit_message_text(
        TEXTS[lang]["question_sent"],
        chat_id=message.chat.id,
        message_id=msg_id,
        reply_markup=kb_back_to_menu(lang)
    )
    await state.clear()
    await state.update_data(lang=lang)

    try:
        await message.bot.send_message(
            MOD_CHAT_ID,
            TEXTS[lang]["question_notification"].format(
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

@router.message(QuestionForm.question)
async def process_question_invalid(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass
        # ============ ПАНЕЛЬ МОДЕРАТОРОВ ============

@router.message(Command("help"), F.chat.id == MOD_CHAT_ID)
async def cmd_help(message: Message) -> None:
    lang = "ru"
    await message.answer(TEXTS[lang]["help_text"])

@router.message(Command("reports"), F.chat.id == MOD_CHAT_ID)
async def cmd_reports(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("📋 Список активных жалоб:", reply_markup=kb_reports_list())

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

@router.callback_query(F.data == "broadcast_cancel")
async def cb_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    try:
        await callback.message.delete()
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
            await message.bot.send_message(user_id, text)
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
    await callback.answer()
    await callback.message.edit_text("📋 Список активных жалоб:", reply_markup=kb_reports_list())

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()

@router.callback_query(F.data == "reject_cancel")
async def cb_reject_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass

@router.callback_query(F.data == "question_reply_cancel")
async def cb_question_reply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("view_"))
async def cb_view_report(callback: CallbackQuery) -> None:
    await callback.answer()
    rid = int(callback.data.split("_", 1)[1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("Жалоба не найдена")
        return
    await callback.message.edit_text(report_caption(rid, r), reply_markup=kb_report_detail(rid, r["user_id"]))

# ============ ДЕЙСТВИЯ МОДЕРАТОРОВ С БАНОМ ============

@router.callback_query(F.data.startswith("ban_unban_"))
async def ban_unban_user(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = int(callback.data.split("_")[-1])
    
    if user_id in BANNED_USERS:
        lang = BANNED_USERS[user_id].get("lang", "ru")
        
        del BANNED_USERS[user_id]
        
        try:
            await callback.bot.send_message(user_id, TEXTS[lang]["unbanned"])
        except Exception:
            pass
        
        try:
            await callback.message.delete()
        except Exception:
            pass
        
        if BANNED_USERS:
            await callback.message.answer("📋 Список забаненных пользователей:", reply_markup=kb_banned_list())
        else:
            await callback.message.answer("📋 Список забаненных пользователей пуст")
    else:
        await callback.message.answer("❌ Пользователь не в бане")

@router.callback_query(F.data.startswith("appeal_approve_"))
async def appeal_approve(callback: CallbackQuery) -> None:
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
        await callback.bot.send_message(user_id, TEXTS[lang]["appeal_approved"])
        await callback.message.edit_text(
            f"✅ Апелляция #{aid} одобрена\nПользователь {appeal['name']} разблокирован"
        )
        logger.info(f"✅ Апелляция #{aid} одобрена, пользователь {user_id} разблокирован")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("appeal_reject_"))
async def appeal_reject(callback: CallbackQuery) -> None:
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
    
    if aid in APPEALS:
        del APPEALS[aid]
    
    try:
        await callback.bot.send_message(user_id, TEXTS[lang]["appeal_rejected"])
        await callback.message.edit_text(
            f"❌ Апелляция #{aid} отклонена\nПользователь {appeal['name']} отправлен в вечный бан"
        )
        logger.info(f"❌ Апелляция #{aid} отклонена, пользователь {user_id} в вечном бане")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

# ============ ДЕЙСТВИЯ МОДЕРАТОРОВ С ЖАЛОБАМИ ============

@router.callback_query(F.data.startswith("mod_approve_"))
async def mod_approve(callback: CallbackQuery) -> None:
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    
    if not r:
        await callback.message.answer("❌ Жалоба не найдена")
        return
    
    lang = r.get("lang", "ru")
    
    try:
        await callback.bot.send_message(r["user_id"], TEXTS[lang]["approved"])
        r["status"] = "approved"
        
        await callback.message.edit_text(
            report_caption(rid, r) + "\n\n✅ Статус: ПРИНЯТА",
            reply_markup=None
        )
        
        logger.info(f"✅ Жалоба #{rid} принята модератором")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {e}")

@router.callback_query(F.data.startswith("mod_reject_"))
async def mod_reject(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    
    if not r:
        await callback.message.answer("❌ Жалоба не найдена")
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
        await message.answer("❌ Жалоба не найдена")
        await state.clear()
        return
    
    lang = r.get("lang", "ru")
    reason = message.text.strip()
    
    try:
        await message.bot.send_message(
            r["user_id"], 
            TEXTS[lang]["rejected_with_msg"].format(msg=reason)
        )
        r["status"] = "rejected"
        
        await message.bot.edit_text(
            chat_id=MOD_CHAT_ID,
            message_id=r["mod_msg_id"],
            text=report_caption(rid, r) + f"\n\n❌ Статус: ОТКЛОНЕНА\n\nПричина: {reason}",
            reply_markup=None
        )
        
        logger.info(f"❌ Жалоба #{rid} отклонена модератором. Причина: {reason}")
        await message.answer(f"✅ Жалоба #{rid} отклонена. Причина отправлена пользователю.")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()

@router.message(ModForm.waiting_reject_reason)
async def process_reject_reason_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

@router.callback_query(F.data.startswith("mod_blocked_"))
async def mod_blocked(callback: CallbackQuery) -> None:
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Жалоба не найдена")
        return

    lang = r.get("lang", "ru")
    target_type = detect_target_type(r["link"])
    type_noun = TARGET_TYPE_NOUN[lang][target_type]
    text = TEXTS[lang]["blocked"].format(link=r["link"], type=type_noun)

    try:
        await callback.bot.send_message(r["user_id"], text)
        r["status"] = "blocked"
        await callback.message.edit_text(
            report_caption(rid, r) + "\n\n🚫 Статус: заблокирован, пользователь уведомлён.",
            reply_markup=None
        )
    except Exception as e:
        await callback.message.answer(f"Ошибка отправки: {e}")

@router.callback_query(F.data.startswith("mod_ban_"))
async def mod_ban(callback: CallbackQuery) -> None:
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Жалоба не найдена")
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

    try:
        await callback.bot.send_message(
            user_id,
            TEXTS[lang]["banned"],
            reply_markup=kb_appeal(lang)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о бане: {e}")

    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

@router.callback_query(F.data.startswith("mod_unban_"))
async def mod_unban(callback: CallbackQuery) -> None:
    await callback.answer()
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.message.answer("❌ Жалоба не найдена")
        return

    user_id = r["user_id"]
    lang = r.get("lang", "ru")
    
    if user_id in BANNED_USERS:
        del BANNED_USERS[user_id]
        try:
            await callback.bot.send_message(user_id, TEXTS[lang]["unbanned"])
        except Exception:
            pass

    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

# ============ ОТВЕТЫ НА ВОПРОСЫ ============

@router.callback_query(F.data.startswith("mod_question_reply_"))
async def mod_question_reply(callback: CallbackQuery, state: FSMContext) -> None:
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
        TEXTS[lang]["question_reply_prompt"],
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
        await message.bot.send_message(
            q["user_id"],
            TEXTS[lang]["question_reply_format"].format(answer=answer)
        )
        q["answered"] = True
        
        await message.answer(f"✅ Ответ отправлен пользователю")
        logger.info(f"❓ Ответ на вопрос #{qid} отправлен пользователю {q['user_id']}")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()

@router.message(ModForm.waiting_question_reply)
async def process_question_reply_invalid(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass

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
