"""
Telegram-бот для приёма жалоб с модерацией
Работает на webhook через порт 10000 для Render.com

Установка:
    pip install aiogram>=3.14.0 aiohttp>=3.10.0

Запуск на Render:
    BOT_TOKEN=ваш_токен python moder.py
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
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
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

# ============ ЛОГИРОВАНИЕ ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============ ХРАНИЛИЩЕ ЖАЛОБ (in-memory) ============
# report_id -> dict(user_id, full_name, username, link, reason, lang, status, mod_msg_id)
REPORTS: dict[int, dict] = {}
_REPORT_COUNTER = 0

# user_id -> {"lang": str, "confirmed": bool}  — запоминаем язык и подтверждение "не робот"
USER_PREFS: dict[int, dict] = {}

# user_id -> datetime последней отправленной жалобы (для кулдауна)
LAST_REPORT_TIME: dict[int, datetime] = {}

# set забаненных пользователей (не могут отправлять жалобы)
BANNED_USERS: set[int] = set()


def next_report_id() -> int:
    global _REPORT_COUNTER
    _REPORT_COUNTER += 1
    return _REPORT_COUNTER


def get_cooldown_remaining_minutes(user_id: int) -> int:
    """Сколько минут осталось до конца кулдауна (0, если кулдаун не активен)."""
    last = LAST_REPORT_TIME.get(user_id)
    if not last:
        return 0
    elapsed = (datetime.utcnow() - last).total_seconds()
    remaining = COOLDOWN_SECONDS - elapsed
    if remaining <= 0:
        return 0
    return math.ceil(remaining / 60)


def detect_target_type(link: str) -> str:
    """Определяет тип нарушителя по ссылке: bot / channel_chat / site."""
    raw = link.strip()
    username = raw.lstrip("@").split("/")[-1] if not raw.lower().startswith(("http://", "https://")) else raw
    # юзернейм бота обычно заканчивается на "bot"
    bare = raw.lstrip("@")
    if bare.lower().endswith("bot") and "/" not in bare and "." not in bare:
        return "bot"
    lowered = raw.lower()
    if lowered.startswith("https://t.me/") or lowered.startswith("http://t.me/") or lowered.startswith("t.me/"):
        # username ссылки на бота вида t.me/name_bot
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
        "confirm_bot_btn": "Я не робот 🤖",
        "enter_link": "Введите ссылку на нарушающий материал:",
        "enter_reason": "Опишите суть жалобы:",
        "sent": "Ваша жалоба отправлена на рассмотрение.",
        "approved": "✅ Ваша жалоба принята!",
        "approved_with_msg": "✅ Ваша жалоба принята!\n\nСообщение для вас: {msg}",
        "rejected": "❌ Ваша жалоба отклонена.",
        "rejected_with_msg": "❌ Ваша жалоба отклонена, {msg}",
        "cooldown": "⏳ Действует кулдаун. Подождите {minutes} мин.",
        "banned": "🚫 Вы заблокированы и не можете отправлять жалобы.",
        "blocked": (
            "Здравствуйте! Вы отправили жалобу на:\n{link}\n\n"
            "После тщательного расследования мы пришли к выводу, что {type} "
            "действительно нарушает правила использования (ToS) и был заблокирован.\n\n"
            "Спасибо, что помогаете бороться с незаконным контентом!"
        ),
    },
    "ua": {
        "choose_lang": "Оберіть мову:",
        "confirm_bot": "Підтвердіть, що ви не робот:",
        "confirm_bot_btn": "Я не робот 🤖",
        "enter_link": "Введіть посилання на матеріал, що порушує правила:",
        "enter_reason": "Опишіть суть скарги:",
        "sent": "Вашу скаргу надіслано на розгляд.",
        "approved": "✅ Вашу скаргу прийнято!",
        "approved_with_msg": "✅ Вашу скаргу прийнято!\n\nПовідомлення для вас: {msg}",
        "rejected": "❌ Вашу скаргу відхилено.",
        "rejected_with_msg": "❌ Вашу скаргу відхилено, {msg}",
        "cooldown": "⏳ Діє кулдаун. Зачекайте {minutes} хв.",
        "banned": "🚫 Вас заблоковано, ви не можете надсилати скарги.",
        "blocked": (
            "Вітаємо! Ви надіслали скаргу на:\n{link}\n\n"
            "Після ретельного розслідування ми дійшли висновку, що {type} "
            "справді порушує правила використання (ToS) і було заблоковано.\n\n"
            "Дякуємо, що допомагаєте боротися з незаконним контентом!"
        ),
    },
    "en": {
        "choose_lang": "Choose language:",
        "confirm_bot": "Please confirm you're not a robot:",
        "confirm_bot_btn": "I'm not a robot 🤖",
        "enter_link": "Enter the link to the violating content:",
        "enter_reason": "Describe the violation:",
        "sent": "Your report has been sent for review.",
        "approved": "✅ Your report has been approved!",
        "approved_with_msg": "✅ Your report has been approved!\n\nMessage for you: {msg}",
        "rejected": "❌ Your report has been rejected.",
        "rejected_with_msg": "❌ Your report has been rejected, {msg}",
        "cooldown": "⏳ Cooldown active. Please wait {minutes} min.",
        "banned": "🚫 You are banned and cannot submit reports.",
        "blocked": (
            "Hello! You submitted a report on:\n{link}\n\n"
            "After a thorough investigation we concluded that the reported {type} "
            "does indeed violate the Terms of Service and has been blocked.\n\n"
            "Thank you for helping fight illegal content!"
        ),
    },
}

# Название типа нарушителя для подстановки в текст "blocked" (в середине предложения)
TARGET_TYPE_NOUN = {
    "ru": {"bot": "бот", "channel_chat": "канал/чат", "site": "сайт"},
    "ua": {"bot": "бот", "channel_chat": "канал/чат", "site": "сайт"},
    "en": {"bot": "bot", "channel_chat": "channel/chat", "site": "website"},
}

# ============ СОСТОЯНИЯ ============

class ReportForm(StatesGroup):
    link = State()
    reason = State()


class ModForm(StatesGroup):
    waiting_msg = State()


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

# ============ ХЕЛПЕРЫ ============

async def cleanup_tracked(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Удаляет все сообщения, накопленные в state за текущую сессию."""
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
    # ============ ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ============

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    # чистим всё, что осталось от прошлой сессии, если она была
    await cleanup_tracked(message.bot, message.chat.id, state)
    try:
        await message.delete()
    except Exception:
        pass
    await state.clear()

    prefs = USER_PREFS.get(user_id)
    lang = prefs["lang"] if prefs else "ru"

    # забаненный пользователь не может отправлять жалобы
    if user_id in BANNED_USERS:
        sent = await message.answer(TEXTS[lang]["banned"])
        await track(state, sent.message_id)
        return

    # проверка кулдауна
    remaining = get_cooldown_remaining_minutes(user_id)
    if remaining > 0:
        sent = await message.answer(TEXTS[lang]["cooldown"].format(minutes=remaining))
        await track(state, sent.message_id)
        return

    # если пользователь уже выбирал язык и подтверждал "не робот" — сразу к вводу ссылки
    if prefs and prefs.get("confirmed"):
        await state.update_data(lang=lang)
        sent = await message.answer(TEXTS[lang]["enter_link"])
        await track(state, sent.message_id)
        await state.update_data(msg_id=sent.message_id)
        await state.set_state(ReportForm.link)
        logger.info(f"👤 Пользователь {user_id} запустил бота (повтор, lang={lang})")
        return

    sent = await message.answer(
        "Выберите язык: / Оберіть мову: / Choose language:",
        reply_markup=kb_language(),
    )
    await track(state, sent.message_id)
    await state.update_data(msg_id=sent.message_id)
    logger.info(f"👤 Пользователь {user_id} запустил бота")

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

    USER_PREFS[callback.from_user.id] = {"lang": lang, "confirmed": True}

    await callback.message.edit_text(TEXTS[lang]["enter_link"])
    await state.update_data(msg_id=callback.message.message_id)
    await state.set_state(ReportForm.link)
    await callback.answer()

@router.message(ReportForm.link, F.text)
async def process_link(message: Message, state: FSMContext) -> None:
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
    # любой не-текст (фото/стикер/и т.п.) на этапе ссылки — просто чистим
    try:
        await message.delete()
    except Exception:
        pass

@router.message(ReportForm.reason, F.text)
async def process_reason(message: Message, state: FSMContext) -> None:
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

    # финальное сообщение пользователю — новое сообщение, затем чистим всё лишнее
    await message.bot.edit_message_text(
        TEXTS[lang]["sent"],
        chat_id=message.chat.id,
        message_id=msg_id
    )
    await cleanup_tracked(message.bot, message.chat.id, state)

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

    await state.clear()

@router.message(ReportForm.reason)
async def process_reason_invalid(message: Message, state: FSMContext) -> None:
    try:
        await message.delete()
    except Exception:
        pass

# ============ ПАНЕЛЬ /reports (только в чате модераторов) ============

@router.message(Command("reports"), F.chat.id == MOD_CHAT_ID)
async def cmd_reports(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("📋 Список активных жалоб:", reply_markup=kb_reports_list())

@router.callback_query(F.data == "reports_list")
async def cb_reports_list(callback: CallbackQuery) -> None:
    await callback.message.edit_text("📋 Список активных жалоб:", reply_markup=kb_reports_list())
    await callback.answer()

@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()

@router.callback_query(F.data.startswith("view_"))
async def cb_view_report(callback: CallbackQuery) -> None:
    rid = int(callback.data.split("_", 1)[1])
    r = REPORTS.get(rid)
    if not r:
        await callback.answer("Жалоба не найдена", show_alert=True)
        return
    await callback.message.edit_text(report_caption(rid, r), reply_markup=kb_report_detail(rid, r["user_id"]))
    await callback.answer()

# ============ ДЕЙСТВИЯ МОДЕРАТОРОВ ============

@router.callback_query(F.data.startswith("mod_approve_"))
async def mod_approve(callback: CallbackQuery, state: FSMContext) -> None:
    rid = int(callback.data.split("_")[-1])
    if rid not in REPORTS:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✏️ Напишите сообщение для пользователя (или отправьте «-» чтобы пропустить)", show_alert=False)

    await state.update_data(action="approve", report_id=rid, panel_chat_id=callback.message.chat.id)
    await state.set_state(ModForm.waiting_msg)

@router.callback_query(F.data.startswith("mod_reject_"))
async def mod_reject(callback: CallbackQuery, state: FSMContext) -> None:
    rid = int(callback.data.split("_")[-1])
    if rid not in REPORTS:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✏️ Напишите сообщение для пользователя (или отправьте «-» чтобы пропустить)", show_alert=False)

    await state.update_data(action="reject", report_id=rid, panel_chat_id=callback.message.chat.id)
    await state.set_state(ModForm.waiting_msg)

@router.callback_query(F.data.startswith("mod_blocked_"))
async def mod_blocked(callback: CallbackQuery) -> None:
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)
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
        )
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    await callback.answer("Готово")

@router.callback_query(F.data.startswith("mod_ban_"))
async def mod_ban(callback: CallbackQuery) -> None:
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)
        return

    BANNED_USERS.add(r["user_id"])

    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

    await callback.answer("🚫 Пользователь забанен")

@router.callback_query(F.data.startswith("mod_unban_"))
async def mod_unban(callback: CallbackQuery) -> None:
    rid = int(callback.data.split("_")[-1])
    r = REPORTS.get(rid)
    if not r:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)
        return

    BANNED_USERS.discard(r["user_id"])

    try:
        await callback.message.edit_reply_markup(reply_markup=kb_report_detail(rid, r["user_id"]))
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=kb_moderator_actions(rid, r["user_id"]))
        except Exception:
            pass

    await callback.answer("✅ Пользователь разбанен")

@router.message(ModForm.waiting_msg, F.text)
async def process_moderator_msg(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action")
    rid = data.get("report_id")
    panel_chat_id = data.get("panel_chat_id")

    r = REPORTS.get(rid)
    moderator_msg = message.text.strip()

    try:
        await message.delete()
    except Exception:
        pass

    if not r:
        await message.answer("❌ Жалоба не найдена (возможно, уже обработана)")
        await state.clear()
        return

    lang = r.get("lang", "ru")
    skip = moderator_msg in ("-", "Отмена", "/cancel", "")

    try:
        if action == "approve":
            text = TEXTS[lang]["approved"] if skip else TEXTS[lang]["approved_with_msg"].format(msg=moderator_msg)
            await message.bot.send_message(r["user_id"], text)
            r["status"] = "approved"
            status_line = "✅ Статус: принята, пользователь уведомлён."
        else:
            text = TEXTS[lang]["rejected"] if skip else TEXTS[lang]["rejected_with_msg"].format(msg=moderator_msg)
            await message.bot.send_message(r["user_id"], text)
            r["status"] = "rejected"
            status_line = "❌ Статус: отклонена, пользователь уведомлён."

        if panel_chat_id:
            try:
                await message.bot.send_message(panel_chat_id, f"{report_caption(rid, r)}\n\n{status_line}")
            except Exception:
                pass

    except Exception as e:
        await message.answer(f"❌ Ошибка отправки пользователю: {e}")

    await state.clear()

@router.message(ModForm.waiting_msg)
async def process_moderator_msg_invalid(message: Message) -> None:
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
    """Пингует сам себя, чтобы бесплатный Render-инстанс не засыпал.

    Пингует чаще и с повторными попытками при неудаче, чтобы минимизировать
    риск того, что инстанс всё же уснёт из-за пропущенного пинга.
    """
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
            await asyncio.sleep(150)  # каждые 2.5 минуты — с запасом до 15-минутного лимита простоя Render

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
