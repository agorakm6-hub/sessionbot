"""
Telegram-бот для приёма жалоб на нарушения (каналы/чаты/боты).

Логика:
1. /start -> выбор языка (RU/UA/EN)
2. Подтверждение "я не робот"
3. Главное меню -> кнопка "Сообщить о нарушении"
4. Пошагово: ссылка/юзернейм -> причина -> доказательство (текст/скриншот)
5. Итоговая сводка с кнопками "Подтвердить" / "Изменить"
6. При подтверждении жалоба отправляется в чат модераторов

Все сообщения бота редактируются (edit_text), а не отправляются заново —
поэтому старое сообщение "исчезает", а новое появляется на его месте.

Установка:
    pip install aiogram==3.13.1

Запуск:
    BOT_TOKEN=ваш_токен python bot.py
"""

import asyncio
import logging
import os

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
)

# ============ НАСТРОЙКИ ============

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬТЕ_СЮДА_ТОКЕН_БОТА")

# ID чата модераторов, куда будут приходить жалобы.
# У вас была ссылка вида tg://chat?id=4354663980 — для supergroup/чата с обсуждением
# в Bot API обычно нужен ID с префиксом -100, т.е. -1004354663980.
# Если бот не сможет отправить сообщение — проверьте этот ID
# (проще всего: добавьте бота в чат админом и перешлите любое сообщение
# из этого чата боту @userinfobot или используйте getUpdates, чтобы узнать точный chat_id).
MOD_CHAT_ID = -1004354663980
# Ссылка на чат модераторов (для справки, в коде не используется): https://t.me/gayclubl

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
    field = callback.data.split("_", 1)[1]  # link / reason / proof

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

    caption = (
        "🚨 Новая жалоба\n\n"
        f"От: {user.full_name} ({username})\n"
        f"ID: {user.id}\n\n"
        f"🔗 Канал/бот: {data.get('link', '-')}\n"
        f"📝 Причина: {data.get('reason', '-')}\n"
    )

    try:
        if data.get("proof_type") == "photo":
            caption += "📎 Доказательство: см. фото"
            await callback.bot.send_photo(MOD_CHAT_ID, data.get("proof"), caption=caption)
        else:
            caption += f"📎 Доказательство: {data.get('proof', '-')}"
            await callback.bot.send_message(MOD_CHAT_ID, caption)
    except Exception as e:
        logging.error("Не удалось отправить жалобу в чат модераторов: %s", e)

    await callback.message.edit_text(TEXTS[lang]["sent"])
    await state.clear()
    await state.update_data(lang=lang)
    await callback.answer()


# ============ ЗАПУСК ============


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
