import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ContentType
)
from aiogram.client.default import DefaultBotProperties

# ==================== НАСТРОЙКИ БОТА ====================
# Вы можете сразу вписать сюда токен в кавычках вместо 'YOUR_BOT_TOKEN_HERE',
# либо оставить переменную окружения (если используется .env файл или хостинг).
TOKEN = "8699932620:AAFczNUE35XQjeedzG9PHcqWqrCwKS4cdI4"

# ID чата модераторов (укажите числовой ID вашего чата или канала)
MODERATOR_CHAT_ID = -4354663980  
# ========================================================

logging.basicConfig(level=logging.INFO)
router = Router()

# Состояния FSM для процесса жалобы
class ReportStates(StatesGroup):
    waiting_for_target = State()
    waiting_for_reason = State()
    waiting_for_evidence = State()

# Словарь текстов для разных языков
TEXTS = {
    "ru": {
        "choose_lang": "🇷🇺 Выберите язык / Please select language:",
        "not_robot": "🤖 Подтвердите, что вы не бот:",
        "robot_btn": "Я не робот 🤖",
        "success_verify": "Спасибо! Вы успешно прошли проверку.",
        "main_menu": "🏠 Главное меню:",
        "report_btn": "🚨 Сообщить о нарушении",
        "enter_target": "📝 Введите ссылку на канал/чат или юзернейм нарушителя:",
        "enter_reason": "❓ Введите причину нарушения:",
        "enter_evidence": "📎 Отправьте ссылку на доказательство или скриншот нарушения:",
        "submitted": "✅ Ваша жалоба будет рассмотрена модераторами.",
        "mod_header": "🚨 <b>Новая жалоба!</b>",
        "mod_user": "👤 <b>От кого:</b>",
        "mod_target": "🎯 <b>Нарушитель:</b>",
        "mod_reason": "📌 <b>Причина:</b>",
        "mod_evidence": "evidence:"
    },
    "uk": {
        "choose_lang": "🇺🇦 Виберіть мову / Please select language:",
        "not_robot": "🤖 Підтвердіть, що ви не бот:",
        "robot_btn": "Я не робот 🤖",
        "success_verify": "Дякую! Ви успішно пройшли перевірку.",
        "main_menu": "🏠 Головне меню:",
        "report_btn": "🚨 Повідомити про порушення",
        "enter_target": "📝 Введіть посилання на канал/чат або юзернейм порушника:",
        "enter_reason": "❓ Введіть причину порушення:",
        "enter_evidence": "📎 Надішліть посилання на доказ або скріншот порушення:",
        "submitted": "✅ Ваша скарга буде розглянута модераторами.",
        "mod_header": "🚨 <b>Нова скарга!</b>",
        "mod_user": "👤 <b>Від кого:</b>",
        "mod_target": "🎯 <b>Порушник:</b>",
        "mod_reason": "📌 <b>Причина:</b>",
        "mod_evidence": "evidence:"
    },
    "en": {
        "choose_lang": "🇬🇧 Please select language:",
        "not_robot": "🤖 Confirm that you are not a bot:",
        "robot_btn": "I'm not a robot 🤖",
        "success_verify": "Thank you! You have successfully verified.",
        "main_menu": "🏠 Main Menu:",
        "report_btn": "🚨 Report a violation",
        "enter_target": "📝 Enter the link to the channel/chat or username of the violator:",
        "enter_reason": "❓ Enter the reason for the violation:",
        "enter_evidence": "📎 Send a link to the evidence or a screenshot of the violation:",
        "submitted": "✅ Your report will be reviewed by moderators.",
        "mod_header": "🚨 <b>New Report!</b>",
        "mod_user": "👤 <b>From:</b>",
        "mod_target": "🎯 <b>Violator:</b>",
        "mod_reason": "📌 <b>Reason:</b>",
        "mod_evidence": "evidence:"
    }
}

def get_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_uk")
        ],
        [
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
    ])

def get_robot_keyboard(lang: str) -> InlineKeyboardMarkup:
    text = TEXTS.get(lang, TEXTS["ru"])["robot_btn"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="not_robot")]
    ])

def get_main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    text = TEXTS.get(lang, TEXTS["ru"])["report_btn"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="report_start")]
    ])

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        text="🇷🇺 Выберите язык / 🇺🇦 Виберіть мову / 🇬🇧 Please select language:",
        reply_markup=get_language_keyboard()
    )

@router.callback_query(F.data.startswith("lang_"))
async def process_language(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(lang=lang)
    
    text = TEXTS[lang]["not_robot"]
    markup = get_robot_keyboard(lang)
    
    await callback.message.edit_text(text=text, reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data == "not_robot")
async def process_not_robot(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    text = TEXTS[lang]["main_menu"]
    markup = get_main_menu_keyboard(lang)
    
    await callback.message.edit_text(text=text, reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data == "report_start")
async def process_report_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    text = TEXTS[lang]["enter_target"]
    await callback.message.edit_text(text=text)
    await state.set_state(ReportStates.waiting_for_target)
    await callback.answer()

@router.message(ReportStates.waiting_for_target)
async def process_target(message: Message, state: FSMContext):
    target = message.text or message.caption
    if not target:
        await message.answer("Пожалуйста, введите текстовую ссылку или юзернейм.")
        return
        
    await state.update_data(target=target)
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    text = TEXTS[lang]["enter_reason"]
    await message.answer(text)
    await state.set_state(ReportStates.waiting_for_reason)

@router.message(ReportStates.waiting_for_reason)
async def process_reason(message: Message, state: FSMContext):
    reason = message.text
    if not reason:
        await message.answer("Пожалуйста, введите причину нарушений текстом.")
        return
        
    await state.update_data(reason=reason)
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    
    text = TEXTS[lang]["enter_evidence"]
    await message.answer(text)
    await state.set_state(ReportStates.waiting_for_evidence)

@router.message(ReportStates.waiting_for_evidence)
async def process_evidence(message: Message, state: FSMContext, bot: Bot):
    evidence_type = None
    evidence_file_id = None
    evidence_text = None

    if message.photo:
        evidence_type = "photo"
        evidence_file_id = message.photo[-1].file_id
    elif message.document:
        evidence_type = "document"
        evidence_file_id = message.document.file_id
    else:
        evidence_type = "text"
        evidence_text = message.text

    data = await state.get_data()
    lang = data.get("lang", "ru")
    target = data.get("target")
    reason = data.get("reason")

    user = message.from_user
    username_str = f"@{user.username}" if user.username else "отсутствует"
    user_link = f"<a href='tg://user?id={user.id}'>{user.full_name}</a>"

    t = TEXTS[lang]
    mod_text = (
        f"{t['mod_header']}\n\n"
        f"{t['mod_user']} {user_link} (ID: <code>{user.id}</code>, Username: {username_str})\n"
        f"{t['mod_target']} {target}\n"
        f"{t['mod_reason']} {reason}\n"
    )

    try:
        if evidence_type == "photo":
            await bot.send_photo(
                chat_id=MODERATOR_CHAT_ID,
                photo=evidence_file_id,
                caption=mod_text + "\n📎 <b>Доказательство (скриншот) выше</b>",
                parse_mode="HTML"
            )
        elif evidence_type == "document":
            await bot.send_document(
                chat_id=MODERATOR_CHAT_ID,
                document=evidence_file_id,
                caption=mod_text + "\n📎 <b>Доказательство (документ) выше</b>",
                parse_mode="HTML"
            )
        else:
            mod_text += f"📎 <b>Доказательство:</b> {evidence_text}"
            await bot.send_message(
                chat_id=MODERATOR_CHAT_ID,
                text=mod_text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    except Exception as e:
        logging.error(f"Не удалось отправить жалобу в чат модераторов: {e}")

    await message.answer(TEXTS[lang]["submitted"])
    
    await message.answer(
        text=TEXTS[lang]["main_menu"],
        reply_markup=get_main_menu_keyboard(lang)
    )
    await state.set_state(None)

async def main():
    # Инициализация бота с настройкой парсера по умолчанию для совместимости с aiogram 3.x
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот запущен и готов к работе.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
