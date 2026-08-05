"""
Telegram-бот для приёма жалоб на нарушения (каналы/чаты/боты).
Работает на webhook через порт 10000 для Render.com

Логика:
1. /start -> выбор языка (RU/UA/EN)
2. Подтверждение "я не робот"
3. Главное меню -> кнопка "Сообщить о нарушении"
4. Пошагово: ссылка/юзернейм -> причина -> доказательство (текст/скриншот)
5. Итоговая сводка с кнопками "Подтвердить" / "Изменить"
6. При подтверждении жалоба отправляется в чат модераторов

Установка:
    pip install aiogram==3.13.1 aiohttp

Запуск на Render:
    BOT_TOKEN=ваш_токен WEBHOOK_URL=https://ваш-бот.onrender.com python moder.py
"""

import asyncio
import logging
import os
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
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

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    print("❌ ОШИБКА: WEBHOOK_URL не установлен!")
    sys.exit(1)

WEBHOOK_PATH = "/webhook"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "10000"))

# ID чата модераторов (из ссылки tg://chat?id=4354663980)
MOD_CHAT_ID = -1004354663980

# ============ ЛОГИРОВАНИЕ ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
    },
}

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

# ============ ХЕНДЛЕРЫ ============

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    sent = await message.answer(
        "Выберите язык: / Оберіть мову: / Choose language:",
        reply_markup=kb_language(),
    )
    await state.update_data(msg_id=sent.message_id)
    logger.info(f"👤 Пользователь {message.from_user.id} (@{message.from_user.username}) запустил бота")

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

@router.callback_query(F.data == "report_confirm")
async def process_report_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    user = callback.from_user
    username = f"@{user.username}" if user.username else "нет юзернейма"

    caption = (
        f"🚨 Новая жалоба\n\n"
        f"👤 От: {user.full_name} ({username})\n"
        f"🆔 ID: {user.id}\n\n"
        f"🔗 Канал/бот: {data.get('link', '-')}\n"
        f"📝 Причина: {data.get('reason', '-')}\n"
    )

    try:
        if data.get("proof_type") == "photo":
            caption += "📎 Доказательство: см. фото ниже"
            await callback.bot.send_photo(MOD_CHAT_ID, data.get("proof"), caption=caption)
        else:
            caption += f"📎 Доказательство: {data.get('proof', '-')}"
            await callback.bot.send_message(MOD_CHAT_ID, caption)
        logger.info(f"✅ Жалоба отправлена в чат модераторов {MOD_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки в чат модераторов: {e}")

    await callback.message.edit_text(TEXTS[lang]["sent"])
    await state.clear()
    await state.update_data(lang=lang)
    await callback.answer()

# ============ WEBHOOK ============

async def webhook_handler(request: web.Request) -> web.Response:
    """Обработчик webhook от Telegram"""
    try:
        data = await request.json()
        update = Update(**data)
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return web.Response(status=500)

async def health_check(request: web.Request) -> web.Response:
    """Health check для Render"""
    return web.json_response({"status": "ok", "bot": "running"})

async def on_startup(app: web.Application) -> None:
    """При запуске сервера"""
    webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
        
        me = await bot.get_me()
        logger.info(f"✅ Бот запущен: @{me.username}")
        
        # Проверяем чат модераторов
        try:
            chat = await bot.get_chat(MOD_CHAT_ID)
            logger.info(f"✅ Чат модераторов: {chat.title}")
        except Exception as e:
            logger.warning(f"⚠️ Не могу проверить чат модераторов: {e}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при старте: {e}")

async def on_shutdown(app: web.Application) -> None:
    """При остановке сервера"""
    try:
        await bot.delete_webhook()
        logger.info("✅ Webhook удалён")
    except Exception as e:
        logger.error(f"❌ Ошибка удаления webhook: {e}")

# ============ ЗАПУСК ============

async def main() -> None:
    global bot, dp
    
    # Инициализация
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Создаем веб-приложение
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    # Запускаем сервер
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    
    logger.info(f"🚀 Сервер запущен на порту {WEB_SERVER_PORT}")
    logger.info(f"📡 Webhook: {WEBHOOK_URL}{WEBHOOK_PATH}")
    
    # Ждем бесконечно
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("⏹️ Остановка...")
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
