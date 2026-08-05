import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest

# Токен бота (рекомендуется передавать через переменные окружения)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
# ID чата модераторов (в Telegram ID супергрупп обычно отрицательные, например, -100...)
MODERATOR_CHAT_ID = int(os.getenv("MODERATOR_CHAT_ID", "-4354663980"))

logging.basicConfig(level=logging.INFO)
router = Router()

# Определение состояний FSM для процесса подачи жалобы
class ReportStates(StatesGroup):
    waiting_for_target = State()
    waiting_for_reason = State()
    waiting_for_proof = State()
    preview_report = State()
    editing_choice = State()
    edit_target = State()
    edit_reason = State()
    edit_proof = State()

# Временное хранилище данных жалоб в памяти (для примера; в продакшене лучше использовать БД)
# Ключ: moderator_message_id, Значение: dict с данными жалобы и user_id
active_reports = {}

# --- Клавиатуры ---

def get_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_uk"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
    ])

def get_captcha_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Я не робот", callback_data="captcha_ok")]
    ])

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚨 Сообщить о нарушении", callback_data="start_report")]
    ])

def get_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="report_confirm")],
        [InlineKeyboardButton(text="✏️ Изменить жалобу", callback_data="report_edit")]
    ])

def get_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылку / Юзернейм", callback_data="edit_target")],
        [InlineKeyboardButton(text="📝 Причину нарушения", callback_data="edit_reason")],
        [InlineKeyboardButton(text="🖼 Доказательство", callback_data="edit_proof")],
        [InlineKeyboardButton(text="🔙 Назад к просмотру", callback_data="edit_back")]
    ])

def get_moderator_keyboard(report_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять жалобу", callback_data=f"mod_accept_{report_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_reject_{report_id}")]
    ])

# --- Хэндлеры ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите язык / Оберіть мову / Choose language:",
        reply_markup=get_language_keyboard()
    )

@router.callback_query(F.data.startswith("lang_"))
async def process_language(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_text(
            "Подтвердите, что вы не бот:",
            reply_markup=get_captcha_keyboard()
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "captcha_ok")
async def process_captcha(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_text(
            "Главное меню:",
            reply_markup=get_main_menu_keyboard()
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "start_report")
async def start_report_process(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ReportStates.waiting_for_target)
    try:
        await callback.message.edit_text("Введите ссылку на канал/чат или юзернейм бота-нарушителя:")
    except TelegramBadRequest:
        pass

@router.message(ReportStates.waiting_for_target)
async def receive_target(message: Message, state: FSMContext):
    await state.update_data(target=message.text.strip())
    await state.set_state(ReportStates.waiting_for_reason)
    await message.answer("Укажите причину нарушения:")

@router.message(ReportStates.waiting_for_reason)
async def receive_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await state.set_state(ReportStates.waiting_for_proof)
    await message.answer("Отправьте ссылку или скриншот нарушения:")

async def show_preview(message: Message, state: FSMContext, is_edit: bool = False):
    data = await state.get_data()
    text = (
        "<b>Проверьте вашу жалобу:</b>\n\n"
        f"🔗 <b>Нарушитель:</b> {data.get('target')}\n"
        f"📝 <b>Причина:</b> {data.get('reason')}\n"
        f"📎 <b>Доказательство:</b> {data.get('proof_type')}"
    )
    
    await state.set_state(ReportStates.preview_report)
    
    if is_edit:
        await message.answer(text, reply_markup=get_preview_keyboard(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=get_preview_keyboard(), parse_mode="HTML")

@router.message(ReportStates.waiting_for_proof, F.text | F.photo)
async def receive_proof(message: Message, state: FSMContext):
    if message.photo:
        proof_data = message.photo[-1].file_id
        proof_type = "фото (скриншот)"
    else:
        proof_data = message.text.strip()
        proof_type = "ссылка"

    await state.update_data(proof_data=proof_data, proof_type=proof_type)
    await show_preview(message, state)

@router.callback_query(F.data == "report_edit", ReportStates.preview_report)
async def report_edit_menu(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ReportStates.editing_choice)
    try:
        await callback.message.edit_text(
            "Что вы хотите изменить?",
            reply_markup=get_edit_keyboard()
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "edit_back", ReportStates.editing_choice)
async def edit_back_to_preview(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    text = (
        "<b>Проверьте вашу жалобу:</b>\n\n"
        f"🔗 <b>Нарушитель:</b> {data.get('target')}\n"
        f"📝 <b>Причина:</b> {data.get('reason')}\n"
        f"📎 <b>Доказательство:</b> {data.get('proof_type')}"
    )
    await state.set_state(ReportStates.preview_report)
    try:
        await callback.message.edit_text(text, reply_markup=get_preview_keyboard(), parse_mode="HTML")
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.in_({"edit_target", "edit_reason", "edit_proof"}), ReportStates.editing_choice)
async def edit_field_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data
    if action == "edit_target":
        await state.set_state(ReportStates.edit_target)
        await callback.message.answer("Введите новую ссылку на канал/чат или юзернейм бота:")
    elif action == "edit_reason":
        await state.set_state(ReportStates.edit_reason)
        await callback.message.answer("Введите новую причину нарушения:")
    elif action == "edit_proof":
        await state.set_state(ReportStates.edit_proof)
        await callback.message.answer("Отправьте новую ссылку или скриншот нарушения:")

@router.message(ReportStates.edit_target)
async def save_edit_target(message: Message, state: FSMContext):
    await state.update_data(target=message.text.strip())
    await show_preview(message, state, is_edit=True)

@router.message(ReportStates.edit_reason)
async def save_edit_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text.strip())
    await show_preview(message, state, is_edit=True)

@router.message(ReportStates.edit_proof, F.text | F.photo)
async def save_edit_proof(message: Message, state: FSMContext):
    if message.photo:
        proof_data = message.photo[-1].file_id
        proof_type = "фото (скриншот)"
    else:
        proof_data = message.text.strip()
        proof_type = "ссылка"

    await state.update_data(proof_data=proof_data, proof_type=proof_type)
    await show_preview(message, state, is_edit=True)

@router.callback_query(F.data == "report_confirm", ReportStates.preview_report)
async def report_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("Жалоба отправлена!")
    data = await state.get_data()
    user = callback.from_user

    user_info = f"@{user.username}" if user.username else "отсутствует"
    user_name = user.full_name

    mod_text = (
        "🚨 <b>Новая жалоба от пользователя!</b>\n\n"
        f"👤 <b>От кого:</b> {user_name} (Юзернейм: {user_info}, ID: <code>{user.id}</code>)\n"
        f"🔗 <b>Нарушитель:</b> {data.get('target')}\n"
        f"📝 <b>Причина жалобы:</b> {data.get('reason')}\n"
        f"📎 <b>Доказательство:</b>"
    )

    proof_data = data.get('proof_data')
    proof_type = data.get('proof_type')

    # Отправка жалобы в чат модераторов
    sent_msg = None
    try:
        # Уникальный ID для кнопок модераторов (используем timestamp или случайный суффикс)
        report_id = str(callback.id)

        if proof_type == "фото (скриншот)":
            sent_msg = await bot.send_photo(
                chat_id=MODERATOR_CHAT_ID,
                photo=proof_data,
                caption=mod_text,
                reply_markup=get_moderator_keyboard(report_id),
                parse_mode="HTML"
            )
        else:
            full_mod_text = f"{mod_text} {proof_data}"
            sent_msg = await bot.send_message(
                chat_id=MODERATOR_CHAT_ID,
                text=full_mod_text,
                reply_markup=get_moderator_keyboard(report_id),
                parse_mode="HTML"
            )

        # Сохраняем связь сообщения модераторов с ID пользователя
        active_reports[str(sent_msg.message_id)] = {
            "user_id": user.id
        }

    except Exception as e:
        logging.error(f"Ошибка при отправке в чат модераторов: {e}")

    try:
        await callback.message.edit_text(
            "Ваша жалоба будет рассмотрена модераторами.",
            reply_markup=None
        )
    except TelegramBadRequest:
        pass

    await state.clear()

@router.callback_query(F.data.startswith("mod_"))
async def moderator_decision(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    action = parts[1] # accept или reject
    report_id = parts[2]

    msg_id = str(callback.message.message_id)
    report_info = active_reports.get(msg_id)

    if not report_info:
        await callback.answer("Информация по этой жалобе не найдена или устарела.", show_alert=True)
        return

    target_user_id = report_info["user_id"]

    try:
        if action == "accept":
            await bot.send_message(
                chat_id=target_user_id,
                text="Модераторы приняли ваш запрос ожидайте."
            )
            decision_text = "\n\n✅ <b>Статус: ЖАЛОБА ПРИНЯТА</b>"
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text="Ваша жалоба была отклонена."
            )
            decision_text = "\n\n❌ <b>Статус: ЖАЛОБА ОТКЛОНЕНА</b>"

        # Обновляем сообщение у модераторов (убираем кнопки и пишем статус)
        if callback.message.photo:
            new_caption = callback.message.caption + decision_text
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.text + decision_text
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")

        await callback.answer("Решение отправлено!")
        active_reports.pop(msg_id, None)

    except Exception as e:
        logging.error(f"Ошибка при обработке решения модератора: {e}")
        await callback.answer("Произошла ошибка при отправке ответа пользователю.", show_alert=True)

async def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Пожалуйста, укажите валидный токен бота в переменной BOT_TOKEN!")
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот успешно запущен и ожидает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
