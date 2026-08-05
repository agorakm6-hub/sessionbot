import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# ТОКЕН БОТА (в реальном проекте лучше передавать через переменные окружения os.getenv("BOT_TOKEN"))
BOT_TOKEN = "7500000000:AAH_EXAMPLE_TOKEN_REPLACE_ME"

# ID чата модераторов
MODERATOR_CHAT_ID = -1004354663980

router = Router()

# Состояния FSM (Конечных автоматов)
class ReportState(StatesGroup):
    waiting_for_target = State()      # Ввод ссылки/юзернейма нарушителя
    waiting_for_reason = State()      # Ввод причины
    waiting_for_evidence = State()    # Ввод доказательства (ссылка или фото)
    preview_report = State()          # Просмотр и подтверждение/изменение жалобы
    editing_choice = State()          # Выбор, что именно изменить

class EditReportState(StatesGroup):
    editing_target = State()
    editing_reason = State()
    editing_evidence = State()

# Переводы интерфейса
TRANSLATIONS = {
    "ru": {
        "lang_chosen": "🇷🇺 Вы выбрали русский язык.",
        "not_bot": "🛡 Подтвердите, что вы не бот:",
        "not_bot_btn": "🤖 Я не робот",
        "main_menu": "🏠 Главное меню модерации:",
        "report_btn": "🚨 Сообщить о нарушении",
        "enter_target": "📝 Введите ссылку на канал/чат или юзернейм нарушителя:",
        "enter_reason": "❓ Введите причину нарушения:",
        "enter_evidence": "📎 Отправьте ссылку на нарушение или прикрепите скриншот:",
        "preview_title": "📋 Проверьте вашу жалобу:\n\n🔗 Нарушитель: {target}\n📄 Причина: {reason}\n доказательство: получено",
        "confirm_btn": "✅ Подтвердить",
        "edit_btn": "✏️ Изменить",
        "success": "✅ Ваша жалоба успешно отправлена модераторам!",
        "edit_menu": "Что именно вы хотите изменить?",
        "edit_target": "🔗 Нарушитель",
        "edit_reason": "📄 Причина",
        "edit_evidence": "📎 Доказательство",
        "back_to_preview": "🔙 Назад к просмотру",
    },
    "uk": {
        "lang_chosen": "🇺🇦 Ви обрали українську мову.",
        "not_bot": "🛡 Підтвердіть, що ви не бот:",
        "not_bot_btn": "🤖 Я не робот",
        "main_menu": "🏠 Головне меню модерації:",
        "report_btn": "🚨 Повідомити про порушення",
        "enter_target": "📝 Введіть посилання на канал/чат або юзернейм порушника:",
        "enter_reason": "❓ Введіть причину порушення:",
        "enter_evidence": "📎 Надішліть посилання на порушення або прикріпіть скріншот:",
        "preview_title": "📋 Перевірте вашу скаргу:\n\n🔗 Порушник: {target}\n📄 Причина: {reason}\n доказательство: получено",
        "confirm_btn": "✅ Підтвердити",
        "edit_btn": "✏️ Змінити",
        "success": "✅ Вашу скаргу успішно надіслано модераторам!",
        "edit_menu": "Що саме ви хочете змінити?",
        "edit_target": "🔗 Порушник",
        "edit_reason": "📄 Причина",
        "edit_evidence": "📎 Доказ",
        "back_to_preview": "🔙 Назад до перегляду",
    },
    "en": {
        "lang_chosen": "🇬🇧 You chose English.",
        "not_bot": "🛡 Please confirm you are not a bot:",
        "not_bot_btn": "🤖 I am not a robot",
        "main_menu": "🏠 Main moderation menu:",
        "report_btn": "🚨 Report a violation",
        "enter_target": "📝 Enter channel/chat link or bot username of the violator:",
        "enter_reason": "❓ Enter the reason for the violation:",
        "enter_evidence": "📎 Send a link to the violation or attach a screenshot:",
        "preview_title": "📋 Review your report:\n\n🔗 Violator: {target}\n📄 Reason: {reason}\n evidence: received",
        "confirm_btn": "✅ Confirm",
        "edit_btn": "✏️ Edit",
        "success": "✅ Your report has been successfully sent to moderators!",
        "edit_menu": "What exactly would you like to change?",
        "edit_target": "🔗 Violator",
        "edit_reason": "📄 Reason",
        "edit_evidence": "📎 Evidence",
        "back_to_preview": "🔙 Back to review",
    }
}

# --- КНОПКИ ---

def get_language_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_uk"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
    ])

def get_captcha_keyboard(lang: str):
    text = TRANSLATIONS[lang]["not_bot_btn"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="captcha_pass")]
    ])

def get_main_menu_keyboard(lang: str):
    text = TRANSLATIONS[lang]["report_btn"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data="start_report")]
    ])

def get_preview_keyboard(lang: str):
    t = TRANSLATIONS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["confirm_btn"], callback_data="report_confirm"),
            InlineKeyboardButton(text=t["edit_btn"], callback_data="report_edit")
        ]
    ])

def get_edit_keyboard(lang: str):
    t = TRANSLATIONS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["edit_target"], callback_data="edit_target")],
        [InlineKeyboardButton(text=t["edit_reason"], callback_data="edit_reason")],
        [InlineKeyboardButton(text=t["edit_evidence"], callback_data="edit_evidence")],
        [InlineKeyboardButton(text=t["back_to_preview"], callback_data="back_to_preview")]
    ])

# --- ХЕНДЛЕРЫ ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    sent_msg = await message.answer(
        "Выберите язык / Оберіть мову / Choose language:",
        reply_markup=get_language_keyboard()
    )
    await state.update_data(last_message_id=sent_msg.message_id)

@router.callback_query(F.data.startswith("lang_"))
async def process_language(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    await state.update_data(lang=lang)
    
    t = TRANSLATIONS[lang]
    await callback.message.edit_text(
        text=f"{t['lang_chosen']}\n\n{t['not_bot']}",
        reply_markup=get_captcha_keyboard(lang)
    )
    await callback.answer()

@router.callback_query(F.data == "captcha_pass")
async def process_captcha(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await callback.message.edit_text(
        text=t["main_menu"],
        reply_markup=get_main_menu_keyboard(lang)
    )
    await callback.answer("Успешно!")

@router.callback_query(F.data == "start_report")
async def start_report_process(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(ReportState.waiting_for_target)
    await callback.message.edit_text(text=t["enter_target"])
    await callback.answer()

@router.message(ReportState.waiting_for_target)
async def receive_target(message: Message, state: FSMContext):
    target = message.text
    if not target:
        return
    await state.update_data(target=target)
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    # Удаляем сообщение пользователя для чистоты интерфейса
    try:
        await message.delete()
    except Exception:
        pass

    msg_id = data.get("last_message_id")
    # Отправляем новое сообщение с запросом причины
    sent = await message.answer(t["enter_reason"])
    await state.update_data(last_message_id=sent.message_id)
    await state.set_state(ReportState.waiting_for_reason)

@router.message(ReportState.waiting_for_reason)
async def receive_reason(message: Message, state: FSMContext):
    reason = message.text
    if not reason:
        return
    await state.update_data(reason=reason)
    
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    try:
        await message.delete()
    except Exception:
        pass

    sent = await message.answer(t["enter_evidence"])
    await state.update_data(last_message_id=sent.message_id)
    await state.set_state(ReportState.waiting_for_evidence)

@router.message(ReportState.waiting_for_evidence)
async def receive_evidence(message: Message, state: FSMContext):
    # Поддерживаем как фото, так и текст (ссылку)
    if message.photo:
        evidence_file_id = message.photo[-1].file_id
        evidence_type = "photo"
    else:
        evidence_file_id = message.text
        evidence_type = "text"
        
    await state.update_data(evidence_file_id=evidence_file_id, evidence_type=evidence_type)
    
    try:
        await message.delete()
    except Exception:
        pass

    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(ReportState.preview_report)
    preview_text = t["preview_title"].format(target=data['target'], reason=data['reason'])
    
    sent = await message.answer(preview_text, reply_markup=get_preview_keyboard(lang))
    await state.update_data(last_message_id=sent.message_id)

@router.callback_query(ReportState.preview_report, F.data == "report_confirm")
async def confirm_report(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    user = callback.from_user
    username_str = f"@{user.username}" if user.username else "отсутствует"
    
    # Формируем текст для чата модераторов
    mod_text = (
        f"🚨 <b>Новая жалоба от пользователя!</b>\n\n"
        f"👤 <b>От кого:</b> {user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"🔗 <b>Юзернейм:</b> {username_str}\n\n"
        f"🎯 <b>Нарушитель:</b> {data['target']}\n"
        f"📄 <b>Причина:</b> {data['reason']}\n"
        f"📎 <b>Доказательство ниже:</b>"
    )
    
    # Отправляем жалобу в чат модераторов
    try:
        if data["evidence_type"] == "photo":
            await bot.send_photo(
                chat_id=MODERATOR_CHAT_ID,
                photo=data["evidence_file_id"],
                caption=mod_text,
                parse_mode="HTML"
            )
        else:
            full_mod_text = f"{mod_text}\n{data['evidence_file_id']}"
            await bot.send_message(
                chat_id=MODERATOR_CHAT_ID,
                text=full_mod_text,
                parse_mode="HTML"
            )
    except Exception as e:
        logging.error(f"Не удалось отправить жалобу модераторам: {e}")

    await callback.message.edit_text(t["success"])
    await state.clear()
    await callback.answer()

@router.callback_query(ReportState.preview_report, F.data == "report_edit")
async def edit_report_menu(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(ReportState.editing_choice)
    await callback.message.edit_text(text=t["edit_menu"], reply_markup=get_edit_keyboard(lang))
    await callback.answer()

@router.callback_query(ReportState.editing_choice, F.data == "edit_target")
async def edit_target_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(EditReportState.editing_target)
    await callback.message.edit_text(text=t["enter_target"])
    await callback.answer()

@router.message(EditReportState.editing_target)
async def save_edited_target(message: Message, state: FSMContext):
    target = message.text
    if not target:
        return
    await state.update_data(target=target)
    
    try:
        await message.delete()
    except Exception:
        pass
        
    await return_to_preview(message, state)

@router.callback_query(ReportState.editing_choice, F.data == "edit_reason")
async def edit_reason_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(EditReportState.editing_reason)
    await callback.message.edit_text(text=t["enter_reason"])
    await callback.answer()

@router.message(EditReportState.editing_reason)
async def save_edited_reason(message: Message, state: FSMContext):
    reason = message.text
    if not reason:
        return
    await state.update_data(reason=reason)
    
    try:
        await message.delete()
    except Exception:
        pass
        
    await return_to_preview(message, state)

@router.callback_query(ReportState.editing_choice, F.data == "edit_evidence")
async def edit_evidence_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(EditReportState.editing_evidence)
    await callback.message.edit_text(text=t["enter_evidence"])
    await callback.answer()

@router.message(EditReportState.editing_evidence)
async def save_edited_evidence(message: Message, state: FSMContext):
    if message.photo:
        evidence_file_id = message.photo[-1].file_id
        evidence_type = "photo"
    else:
        evidence_file_id = message.text
        evidence_type = "text"
        
    await state.update_data(evidence_file_id=evidence_file_id, evidence_type=evidence_type)
    
    try:
        await message.delete()
    except Exception:
        pass
        
    await return_to_preview(message, state)

@router.callback_query(ReportState.editing_choice, F.data == "back_to_preview")
async def back_to_preview_cb(callback: CallbackQuery, state: FSMContext):
    await return_to_preview_callback(callback, state)
    await callback.answer()

async def return_to_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(ReportState.preview_report)
    preview_text = t["preview_title"].format(target=data['target'], reason=data['reason'])
    
    msg_id = data.get("last_message_id")
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text=preview_text,
            reply_markup=get_preview_keyboard(lang)
        )
    except Exception:
        sent = await message.answer(preview_text, reply_markup=get_preview_keyboard(lang))
        await state.update_data(last_message_id=sent.message_id)

async def return_to_preview_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = TRANSLATIONS[lang]
    
    await state.set_state(ReportState.preview_report)
    preview_text = t["preview_title"].format(target=data['target'], reason=data['reason'])
    
    await callback.message.edit_text(
        text=preview_text,
        reply_markup=get_preview_keyboard(lang)
    )

# --- ЗАПУСК БОТА ---

async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    
    dp.include_router(router)
    
    print("Бот запущен и готов к работе...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
