"""
Telegram-бот для приёма жалоб на нарушения (каналы/чаты/боты).
С поддержкой webhook на порту 10000, инлайн кнопками для модераторов,
командой /reports и проверкой деплоя.

Логика:
1. /start -> выбор языка (RU/UA/EN)
2. Подтверждение "я не робот"
3. Главное меню -> кнопка "Сообщить о нарушении"
4. Пошагово: ссылка/юзернейм -> причина -> доказательство
5. Итоговая сводка с кнопками "Подтвердить" / "Изменить"
6. При подтверждении жалоба отправляется в чат модераторов с кнопками
7. /reports - просмотр всех жалоб

Установка:
    pip install aiogram==3.13.1 aiohttp python-dotenv

Запуск:
    export BOT_TOKEN="ваш_токен"
    export WEBHOOK_URL="https://ваш_домен"
    python moder_v2.py
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

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
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ============ НАСТРОЙКИ ============

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ТОКЕН_БОТА")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://example.com")
WEBHOOK_PATH = "/webhook/telegram"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", "10000"))

# ID чата модераторов
MOD_CHAT_ID = int(os.getenv("MOD_CHAT_ID", "-1004354663980"))

# ID главного модератора (может использовать /reports)
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# Файл для хранения жалоб (DSA compliance)
REPORTS_FILE = "reports_storage.json"

# ============ ЛОГИРОВАНИЕ ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============ ХРАНИЛИЩЕ ЖАЛОБ ============

class ReportsStorage:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.reports = self._load()

    def _load(self) -> list:
        if self.file_path.exists():
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.reports, f, ensure_ascii=False, indent=2)

    def add_report(self, data: dict) -> int:
        report = {
            "id": len(self.reports) + 1,
            "timestamp": datetime.now().isoformat(),
            "user_id": data.get("user_id"),
            "user_name": data.get("user_name"),
            "username": data.get("username"),
            "link": data.get("link"),
            "reason": data.get("reason"),
            "proof": data.get("proof"),
            "proof_type": data.get("proof_type"),
            "lang": data.get("lang"),
            "status": "pending",  # pending, approved, rejected
            "moderator_notes": ""
        }
        self.reports.append(report)
        self._save()
        logger.info(f"📝 Жалоба #{report['id']} добавлена")
        return report["id"]

    def get_all(self) -> list:
        return self.reports

    def get_by_id(self, report_id: int) -> dict:
        for report in self.reports:
            if report["id"] == report_id:
                return report
        return None

    def update_status(self, report_id: int, status: str, notes: str = ""):
        for report in self.reports:
            if report["id"] == report_id:
                report["status"] = status
                report["moderator_notes"] = notes
                self._save()
                logger.info(f"✏️ Жалоба #{report_id} обновлена: {status}")
                return True
        return False

reports_storage = ReportsStorage(REPORTS_FILE)

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
        "summary": (
            "Проверьте вашу жалобу:\n\n"
            "🔗 Канал/бот: {link}\n"
            "📝 Причина: {reason}\n"
            "📎 Доказательство: {proof}"
        ),
        "confirm_btn": "✅ Подтвердить",
        "edit_btn": "✏️ Изменить",
        "edit_choose": "Что вы хотите изменить?",
        "edit_link_btn": "Ссылку/юзернейм",
        "edit_reason_btn": "Причину",
        "edit_proof_btn": "Доказательство",
        "back_btn": "⬅️ Назад",
        "sent": "✅ Ваша жалоба отправлена модераторам и будет рассмотрена в ближайшее время. Спасибо!",
        "photo_placeholder": "[скриншот]",
        "approve_btn": "✅ Одобрить",
        "reject_btn": "❌ Отклонить",
        "note_btn": "📝 Заметка",
        "report_id": "📋 ID жалобы",
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
        "summary": (
            "Перевірте вашу скаргу:\n\n"
            "🔗 Канал/бот: {link}\n"
            "📝 Причина: {reason}\n"
            "📎 Доказ: {proof}"
        ),
        "confirm_btn": "✅ Підтвердити",
        "edit_btn": "✏️ Змінити",
        "edit_choose": "Що ви хочете змінити?",
        "edit_link_btn": "Посилання/юзернейм",
        "edit_reason_btn": "Причину",
        "edit_proof_btn": "Доказ",
        "back_btn": "⬅️ Назад",
        "sent": "✅ Вашу скаргу надіслано модераторам, її розглянуть найближчим часом. Дякуємо!",
        "photo_placeholder": "[скріншот]",
        "approve_btn": "✅ Одобрити",
        "reject_btn": "❌ Відхилити",
        "note_btn": "📝 Замітка",
        "report_id": "📋 ID скарги",
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
        "summary": (
            "Please check your report:\n\n"
            "🔗 Channel/bot: {link}\n"
            "📝 Reason: {reason}\n"
            "📎 Proof: {proof}"
        ),
        "confirm_btn": "✅ Confirm",
        "edit_btn": "✏️ Edit",
        "edit_choose": "What do you want to edit?",
        "edit_link_btn": "Link/username",
        "edit_reason_btn": "Reason",
        "edit_proof_btn": "Proof",
        "back_btn": "⬅️ Back",
        "sent": "✅ Your report has been sent to the moderators and will be reviewed soon. Thank you!",
        "photo_placeholder": "[screenshot]",
        "approve_btn": "✅ Approve",
        "reject_btn": "❌ Reject",
        "note_btn": "📝 Note",
        "report_id": "📋 Report ID",
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

def kb_moderator_actions(report_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    """Клавиатура для модераторов под сообщением жалобы"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=TEXTS[lang]["approve_btn"], callback_data=f"mod_approve_{report_id}"),
                InlineKeyboardButton(text=TEXTS[lang]["reject_btn"], callback_data=f"mod_reject_{report_id}"),
            ],
            [InlineKeyboardButton(text=TEXTS[lang]["note_btn"], callback_data=f"mod_note_{report_id}")],
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
    username = f"@{user.username}" if user.username else "нет юзернейма"

    # Сохраняем жалобу в хранилище
    report_data = {
        "user_id": user.id,
        "user_name": user.full_name,
        "username": username,
        "link": data.get("link", "-"),
        "reason": data.get("reason", "-"),
        "proof": data.get("proof", "-"),
        "proof_type": data.get("proof_type", "text"),
        "lang": lang,
    }
    report_id = reports_storage.add_report(report_data)

    caption = (
        f"🚨 Новая жалоба #{report_id}\n\n"
        f"От: {user.full_name} ({username})\n"
        f"ID: {user.id}\n\n"
        f"🔗 Канал/бот: {data.get('link', '-')}\n"
        f"📝 Причина: {data.get('reason', '-')}\n"
    )

    try:
        if data.get("proof_type") == "photo":
            caption += "📎 Доказательство: см. фото"
            await callback.bot.send_photo(
                MOD_CHAT_ID,
                data.get("proof"),
                caption=caption,
                reply_markup=kb_moderator_actions(report_id, lang)
            )
        else:
            caption += f"📎 Доказательство: {data.get('proof', '-')}"
            await callback.bot.send_message(
                MOD_CHAT_ID,
                caption,
                reply_markup=kb_moderator_actions(report_id, lang)
            )
        logger.info(f"✅ Жалоба #{report_id} отправлена модераторам")
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке жалобы: {e}")

    await callback.message.edit_text(TEXTS[lang]["sent"])
    await state.clear()
    await state.update_data(lang=lang)
    await callback.answer()

# ============ ХЕНДЛЕРЫ: МОДЕРАТОР ДЕЙСТВИЯ ============

@router.callback_query(F.data.startswith("mod_approve_"))
async def process_mod_approve(callback: CallbackQuery) -> None:
    report_id = int(callback.data.split("_")[-1])
    report = reports_storage.get_by_id(report_id)
    
    if report:
        reports_storage.update_status(report_id, "approved")
        lang = report.get("lang", "ru")
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n✅ Одобрено модератором @{callback.from_user.username or callback.from_user.id}",
            reply_markup=None
        )
        await callback.answer("✅ Жалоба одобрена", show_alert=False)
    else:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)

@router.callback_query(F.data.startswith("mod_reject_"))
async def process_mod_reject(callback: CallbackQuery) -> None:
    report_id = int(callback.data.split("_")[-1])
    report = reports_storage.get_by_id(report_id)
    
    if report:
        reports_storage.update_status(report_id, "rejected")
        await callback.message.edit_caption(
            caption=callback.message.caption + f"\n\n❌ Отклонено модератором @{callback.from_user.username or callback.from_user.id}",
            reply_markup=None
        )
        await callback.answer("❌ Жалоба отклонена", show_alert=False)
    else:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)

@router.callback_query(F.data.startswith("mod_note_"))
async def process_mod_note(callback: CallbackQuery) -> None:
    report_id = int(callback.data.split("_")[-1])
    report = reports_storage.get_by_id(report_id)
    
    if report:
        await callback.answer("📝 Напишите заметку в чат", show_alert=False)
    else:
        await callback.answer("❌ Жалоба не найдена", show_alert=True)

# ============ КОМАНДЫ ============

@router.message(Command("reports"))
async def cmd_reports(message: Message) -> None:
    """Команда для просмотра всех жалоб (только для админа)"""
    
    # Проверка прав доступа
    if message.from_user.id != ADMIN_USER_ID and ADMIN_USER_ID != 0:
        await message.answer("❌ У вас нет доступа к этой команде")
        return

    all_reports = reports_storage.get_all()
    
    if not all_reports:
        await message.answer("📋 Жалоб нет")
        return

    # Формируем список жалоб
    report_text = "📋 **ВСЕ ЖАЛОБЫ**\n\n"
    
    for report in all_reports:
        status_emoji = {
            "pending": "⏳",
            "approved": "✅",
            "rejected": "❌"
        }.get(report.get("status"), "❓")
        
        report_text += (
            f"{status_emoji} **Жалоба #{report['id']}**\n"
            f"⏰ {report['timestamp'][:10]}\n"
            f"👤 {report['user_name']} ({report['username']})\n"
            f"🔗 {report['link']}\n"
            f"📝 {report['reason']}\n"
            f"Статус: {report['status']}\n"
        )
        if report.get('moderator_notes'):
            report_text += f"📌 {report['moderator_notes']}\n"
        report_text += "—\n"

    # Отправляем по частям если текст большой
    if len(report_text) > 4096:
        for i in range(0, len(report_text), 4096):
            await message.answer(report_text[i:i+4096], parse_mode="Markdown")
    else:
        await message.answer(report_text, parse_mode="Markdown")

# ============ WEBHOOK И WEB SERVER ============

async def webhook_handler(request: web.Request) -> web.Response:
    """Обработчик webhook'а"""
    try:
        update = Update(**await request.json())
        await dp.feed_update(bot, update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)

async def health_check(request: web.Request) -> web.Response:
    """Проверка здоровья сервера"""
    return web.json_response({"status": "ok", "bot_running": True})

async def on_startup(app: web.Application) -> None:
    """При запуске сервера"""
    try:
        await bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")
        logger.info(f"✅ Webhook установлен: {WEBHOOK_URL}{WEBHOOK_PATH}")
    except Exception as e:
        logger.error(f"❌ Ошибка при установке webhook: {e}")

async def on_shutdown(app: web.Application) -> None:
    """При остановке сервера"""
    try:
        await bot.delete_webhook()
        logger.info("Webhook удалён")
    except Exception as e:
        logger.error(f"Ошибка при удалении webhook: {e}")

async def main() -> None:
    """Главная функция"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    global bot, dp
    
    # Инициализация бота и диспетчера
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Создание web приложения
    app = web.Application()
    
    # Маршруты
    app.router.add_post(WEBHOOK_PATH, webhook_handler)
    app.router.add_get("/health", health_check)
    app.router.add_get("/", health_check)
    
    # Startup/Shutdown обработчики
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Запуск сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
    await site.start()
    
    logger.info(f"🚀 Сервер запущен на {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    logger.info(f"📡 Webhook URL: {WEBHOOK_URL}{WEBHOOK_PATH}")
    logger.info(f"💾 Хранилище жалоб: {REPORTS_FILE}")

    # Проверка подключения
    try:
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения бота: {e}")

    # Бесконечный цикл
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
