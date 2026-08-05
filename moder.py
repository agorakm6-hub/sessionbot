"""
Telegram-бот для приёма жалоб с модерацией
Работает на webhook через порт 10000 для Render.com

Установка:
    pip install aiogram==3.13.1 aiohttp

Запуск на Render:
    BOT_TOKEN=ваш_токен python moder.py
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

WEBHOOK_PATH = "/webhook"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "10000"))

# ID чата модераторов
MOD_CHAT_ID = -1004354663980

# ============ ЛОГИРОВАНИЕ ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============ ТЕКСТЫ ============

TEXTS = {
    "ru": {
        "choose_lang": "Выберите язык:",
        "confirm_bot": "Подтвердите, что вы не робот:",
        "confirm_bot_btn": "Я не робот 🤖",
        "enter_link": "Введите ссылку на нарушение (канал, чат, сайт, юзернейм и т.д.):",
        "enter_reason": "Опишите суть жалобы:",
        "sent": "✅ Ваша жалоба отправлена модераторам и будет рассмотрена.",
        "approved": "✅ Ваша жалоба принята!",
        "approved_with_msg": "✅ Ваша жалоба принята!\n\nСообщение для вас: {msg}",
        "rejected": "❌ Ваша жалоба отклонена.",
        "rejected_with_msg": "❌ Ваша жалоба отклонена.\n\nСообщение для вас: {msg}",
    },
    "ua": {
        "choose_lang": "Оберіть мову:",
        "confirm_bot": "Підтвердіть, що ви не робот:",
        "confirm_bot_btn": "Я не робот 🤖",
        "enter_link": "Введіть посилання на порушення (канал, чат, сайт, юзернейм тощо):",
        "enter_reason": "Опишіть суть скарги:",
        "sent": "✅ Вашу скаргу надіслано модераторам, її розглянуть.",
        "approved": "✅ Вашу скаргу прийнято!",
        "approved_with_msg": "✅ Вашу скаргу прийнято!\n\nПовідомлення для вас: {msg}",
        "rejected": "❌ Вашу скаргу відхилено.",
        "rejected_with_msg": "❌ Вашу скаргу відхилено.\n\nПовідомлення для вас: {msg}",
    },
    "en": {
        "choose_lang": "Choose language:",
        "confirm_bot": "Please confirm you're not a robot:",
        "confirm_bot_btn": "I'm not a robot 🤖",
        "enter_link": "Enter the violation link (channel, chat, website, username, etc.):",
        "enter_reason": "Describe the violation:",
        "sent": "✅ Your report has been sent to moderators.",
        "approved": "✅ Your report has been approved!",
        "approved_with_msg": "✅ Your report has been approved!\n\nMessage for you: {msg}",
        "rejected": "❌ Your report has been rejected.",
        "rejected_with_msg": "❌ Your report has been rejected.\n\nMessage for you: {msg}",
    },
}

# ============ СОСТОЯНИЯ ============

class ReportForm(StatesGroup):
    link = State()
    reason = State()

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

def kb_moderator_actions(report_id: int, lang: str) -> InlineKeyboardMarkup:
    """Кнопки для модераторов"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"mod_approve_{report_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_reject_{report_id}"),
            ]
        ]
    )

# ============ ХЕНДЛЕРЫ ============

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    sent = await message.answer(
        "Выберите язык: / Оберіть мову: / Choose language:",
        reply_markup=kb_language(),
    )
    await state.update_data(msg_id=sent.message_id)
    logger.info(f"👤 Пользователь {message.from_user.id} запустил бота")

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
    
    # Сразу просим ссылку
    await callback.message.edit_text(TEXTS[lang]["enter_link"])
    await state.update_data(msg_id=callback.message.message_id)
    await state.set_state(ReportForm.link)
    await callback.answer()

@router.message(ReportForm.link, F.text)
async def process_link(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")

    await state.update_data(link=message.text)
    
    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass
    
    await message.bot.edit_message_text(
        TEXTS[lang]["enter_reason"], 
        chat_id=message.chat.id, 
        message_id=msg_id
    )
    await state.set_state(ReportForm.reason)

@router.message(ReportForm.reason, F.text)
async def process_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    msg_id = data.get("msg_id")
    link = data.get("link")
    reason = message.text

    # Удаляем сообщение пользователя
    try:
        await message.delete()
    except:
        pass

    # Сохраняем данные
    await state.update_data(reason=reason, user_id=message.from_user.id)

    # Отправляем "жалоба отправлена"
    await message.bot.edit_message_text(
        TEXTS[lang]["sent"],
        chat_id=message.chat.id,
        message_id=msg_id
    )

    # Отправляем жалобу модераторам
    user = message.from_user
    username = f"@{user.username}" if user.username else "нет юзернейма"
    
    # Генерируем ID жалобы (используем timestamp)
    report_id = int(datetime.now().timestamp())
    await state.update_data(report_id=report_id)

    caption = (
        f"🚨 Новая жалоба\n\n"
        f"👤 От: {user.full_name} ({username})\n"
        f"🆔 ID: {user.id}\n\n"
        f"🔗 Ссылка: {link}\n"
        f"📝 Текст: {reason}"
    )

    try:
        await message.bot.send_message(
            MOD_CHAT_ID,
            caption,
            reply_markup=kb_moderator_actions(report_id, lang)
        )
        logger.info(f"✅ Жалоба #{report_id} отправлена модераторам")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки модераторам: {e}")

    await state.clear()

# ============ МОДЕРАТОРЫ ============

@router.callback_query(F.data.startswith("mod_approve_"))
async def mod_approve(callback: CallbackQuery, state: FSMContext) -> None:
    report_id = int(callback.data.split("_")[-1])
    
    # Получаем данные из сообщения
    msg_text = callback.message.text or callback.message.caption
    lines = msg_text.split('\n')
    
    # Ищем ID пользователя
    user_id = None
    for line in lines:
        if '🆔 ID:' in line:
            user_id = int(line.split('🆔 ID:')[1].strip())
            break
    
    if not user_id:
        await callback.answer("❌ Не найден ID пользователя", show_alert=True)
        return
    
    # Удаляем кнопки у сообщения модераторов
    await callback.message.edit_reply_markup(reply_markup=None)
    
    # Спрашиваем что написать пользователю
    await callback.answer("✏️ Напишите сообщение для пользователя (или нажмите Отмена)", show_alert=False)
    
    # Ждем ответ от модератора
    await state.update_data(action="approve", user_id=user_id, report_id=report_id)
    await state.set_state("waiting_moderator_msg")

@router.callback_query(F.data.startswith("mod_reject_"))
async def mod_reject(callback: CallbackQuery, state: FSMContext) -> None:
    report_id = int(callback.data.split("_")[-1])
    
    # Получаем данные из сообщения
    msg_text = callback.message.text or callback.message.caption
    lines = msg_text.split('\n')
    
    # Ищем ID пользователя
    user_id = None
    for line in lines:
        if '🆔 ID:' in line:
            user_id = int(line.split('🆔 ID:')[1].strip())
            break
    
    if not user_id:
        await callback.answer("❌ Не найден ID пользователя", show_alert=True)
        return
    
    # Удаляем кнопки у сообщения модераторов
    await callback.message.edit_reply_markup(reply_markup=None)
    
    # Спрашиваем что написать пользователю
    await callback.answer("✏️ Напишите сообщение для пользователя (или нажмите Отмена)", show_alert=False)
    
    # Ждем ответ от модератора
    await state.update_data(action="reject", user_id=user_id, report_id=report_id)
    await state.set_state("waiting_moderator_msg")

@router.message(F.text, StateFilter("waiting_moderator_msg"))
async def process_moderator_msg(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    action = data.get("action")
    user_id = data.get("user_id")
    report_id = data.get("report_id")
    lang = "ru"  # Можно определить по языку пользователя, но для простоты оставим ru
    
    moderator_msg = message.text
    
    # Удаляем сообщение модератора
    try:
        await message.delete()
    except:
        pass
    
    # Отправляем пользователю
    try:
        if action == "approve":
            if moderator_msg and moderator_msg != "Отмена" and moderator_msg != "/cancel":
                text = TEXTS[lang]["approved_with_msg"].format(msg=moderator_msg)
            else:
                text = TEXTS[lang]["approved"]
            
            await message.bot.send_message(user_id, text)
            await message.answer(f"✅ Жалоба #{report_id} принята! Пользователь уведомлен.")
            
        elif action == "reject":
            if moderator_msg and moderator_msg != "Отмена" and moderator_msg != "/cancel":
                text = TEXTS[lang]["rejected_with_msg"].format(msg=moderator_msg)
            else:
                text = TEXTS[lang]["rejected"]
            
            await message.bot.send_message(user_id, text)
            await message.answer(f"❌ Жалоба #{report_id} отклонена! Пользователь уведомлен.")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки пользователю: {e}")
    
    await state.clear()

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

async def on_shutdown(app: web.Application) -> None:
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
    # Добавляем импорт datetime
    from datetime import datetime
    asyncio.run(main())
