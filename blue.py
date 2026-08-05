#!/usr/bin/env python3
import logging
import os
import sys
import asyncio
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes,
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT")
PORT = int(os.getenv("PORT", "10000"))
EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBHOOK_URL")
MODERATOR_CHAT_ID = -4354663980

if not BOT_TOKEN:
    logger.error("❌ Не задана переменная TELEGRAM_BOT")
    sys.exit(1)
if not EXTERNAL_URL:
    logger.error("❌ Не найдена переменная RENDER_EXTERNAL_URL или WEBHOOK_URL")
    sys.exit(1)

logger.info("✅ Проверка переменных окружения пройдена")

LANGUAGE, VERIFICATION, MAIN_MENU = range(1, 4)
REPORT_STEP_1, REPORT_STEP_2, REPORT_STEP_3 = range(4, 7)
REPORT_CONFIRM, REPORT_EDIT = range(7, 9)

TEXTS = {
    'ru': {
        'select_language': '🌐 Выберите язык:', 'verify': '🤖 Подтвердите что вы не бот', 'verify_button': '✅ Я не робот',
        'main_menu': '📋 Главное меню', 'report_button': '📢 Сообщить об нарушении', 'back_button': '⬅️ Назад',
        'step1': '🔗 Введите ссылку на канал/чат или юзернейм бота нарушителя:',
        'step2': '⚠️ Введите причину нарушения:', 'step3': '📸 Введите ссылку или скриншот нарушения:',
        'confirm_text': '✅ Проверьте данные жалобы:\n\n🔗 <b>Канал/Бот:</b> {target}\n\n⚠️ <b>Причина:</b> {reason}\n\n📸 <b>Доказательство:</b> {evidence}\n\nВсё верно?',
        'edit_button': '✏️ Изменить', 'confirm_button': '✅ Подтвердить', 'edit_options': 'Что изменить?',
        'edit_target': '🔗 Ссылка/юзернейм', 'edit_reason': '⚠️ Причина', 'edit_evidence': '📸 Доказательство',
        'success': '✅ Ваша жалоба отправлена модераторам!\n\n📌 ID жалобы: <b>{report_id}</b>',
        'accepted': '✅ Модераторы приняли вашу жалобу!\n\n📌 ID: <b>{report_id}</b>',
        'rejected': '❌ Модераторы отклонили вашу жалобу.\n\n📌 ID: <b>{report_id}</b>',
    },
    'ua': {
        'select_language': '🌐 Виберіть мову:', 'verify': '🤖 Підтвердіть що ви не бот', 'verify_button': '✅ Я не робот',
        'main_menu': '📋 Головне меню', 'report_button': '📢 Повідомити про порушення', 'back_button': '⬅️ Назад',
        'step1': '🔗 Введіть посилання на канал/чат або ім\'я користувача бота:',
        'step2': '⚠️ Введіть причину порушення:', 'step3': '📸 Введіть посилання або скріншот порушення:',
        'confirm_text': '✅ Перевірте дані скарги:\n\n🔗 <b>Канал/Бот:</b> {target}\n\n⚠️ <b>Причина:</b> {reason}\n\n📸 <b>Доказ:</b> {evidence}\n\nВсе правильно?',
        'edit_button': '✏️ Змінити', 'confirm_button': '✅ Підтвердити', 'edit_options': 'Що змінити?',
        'edit_target': '🔗 Посилання/ім\'я', 'edit_reason': '⚠️ Причина', 'edit_evidence': '📸 Доказ',
        'success': '✅ Вашу скаргу відправлено модераторам!\n\n📌 ID скарги: <b>{report_id}</b>',
        'accepted': '✅ Модератори прийняли вашу скаргу!\n\n📌 ID: <b>{report_id}</b>',
        'rejected': '❌ Модератори відхилили вашу скаргу.\n\n📌 ID: <b>{report_id}</b>',
    },
    'en': {
        'select_language': '🌐 Select language:', 'verify': '🤖 Verify that you are not a bot', 'verify_button': '✅ I\'m not a robot',
        'main_menu': '📋 Main Menu', 'report_button': '📢 Report a violation', 'back_button': '⬅️ Back',
        'step1': '🔗 Enter the channel/chat link or bot username:', 'step2': '⚠️ Enter the reason for the violation:',
        'step3': '📸 Enter the violation link or screenshot:',
        'confirm_text': '✅ Check the complaint data:\n\n🔗 <b>Channel/Bot:</b> {target}\n\n⚠️ <b>Reason:</b> {reason}\n\n📸 <b>Evidence:</b> {evidence}\n\nIs everything correct?',
        'edit_button': '✏️ Edit', 'confirm_button': '✅ Confirm', 'edit_options': 'What to change?',
        'edit_target': '🔗 Link/Username', 'edit_reason': '⚠️ Reason', 'edit_evidence': '📸 Evidence',
        'success': '✅ Your complaint has been sent to moderators!\n\n📌 Complaint ID: <b>{report_id}</b>',
        'accepted': '✅ Moderators accepted your complaint!\n\n📌 ID: <b>{report_id}</b>',
        'rejected': '❌ Moderators rejected your complaint.\n\n📌 ID: <b>{report_id}</b>',
    }
}

def get_text(lang, key, **kwargs):
    text = TEXTS.get(lang, TEXTS['en']).get(key, '')
    return text.format(**kwargs) if kwargs else text

application = None
anti_sleep = None

class AntySleepSystem:
    def __init__(self, bot_app, interval_minutes=4):
        self.app = bot_app
        self.interval = interval_minutes * 60
        self.active = False
        self.last_ping = datetime.now()
        self.ping_count = 0
        
    async def start(self):
        self.active = True
        logger.info(f"⏰ Anti-sleep система запущена (пинг каждые {self.interval // 60} минут)")
        while self.active:
            try:
                await asyncio.sleep(self.interval)
                await self.ping()
            except Exception as e:
                logger.error(f"❌ Ошибка в anti-sleep: {e}")
    
    async def ping(self):
        try:
            self.ping_count += 1
            self.last_ping = datetime.now()
            me = await self.app.bot.get_me()
            logger.info(f"🟢 Пинг #{self.ping_count} - Бот активен: @{me.username} ({self.last_ping.strftime('%H:%M:%S')})")
            try:
                webhook_info = await self.app.bot.get_webhook_info()
                if webhook_info.url:
                    logger.info(f"🔗 Webhook активен: {webhook_info.url}")
            except Exception as e:
                logger.warning(f"⚠️ Webhook check: {e}")
        except Exception as e:
            logger.error(f"❌ Ping failed: {e}")
    
    def stop(self):
        self.active = False
        logger.info("⛔ Anti-sleep система остановлена")
    
    def get_status(self):
        return {'active': self.active, 'ping_count': self.ping_count, 'last_ping': self.last_ping.isoformat(), 'interval_minutes': self.interval // 60}

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    
    def do_POST(self):
        if self.path == f'/bot{BOT_TOKEN}':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            try:
                update = json.loads(body)
                asyncio.run(application.process_update(Update.de_json(update, application.bot)))
            except Exception as e:
                logger.error(f"❌ Webhook error: {e}")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
    
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            health_data = {'status': 'ok', 'timestamp': datetime.now().isoformat(), 'bot_token_set': bool(BOT_TOKEN), 'anti_sleep': anti_sleep.get_status() if anti_sleep else None}
            self.wfile.write(json.dumps(health_data).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Moderator Bot is running')
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data['user_id'] = user.id
    context.user_data['username'] = user.username or 'Unknown'
    context.user_data['first_name'] = user.first_name or ''
    context.user_data['message_id'] = None
    
    keyboard = [[InlineKeyboardButton('🇷🇺 Русский', callback_data='lang_ru'), InlineKeyboardButton('🇺🇦 Українська', callback_data='lang_ua'), InlineKeyboardButton('🇬🇧 English', callback_data='lang_en')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = await update.message.reply_text(get_text('en', 'select_language'), reply_markup=reply_markup)
    
    context.user_data['message_id'] = message.message_id
    return LANGUAGE

async def select_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = query.data.split('_')[1]
    context.user_data['language'] = lang
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'verify_button'), callback_data='verify_ok')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=query.from_user.id,
        message_id=context.user_data['message_id'],
        text=get_text(lang, 'verify'),
        reply_markup=reply_markup
    )
    
    return VERIFICATION

async def verify_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = context.user_data.get('language', 'en')
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'report_button'), callback_data='report')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=query.from_user.id,
        message_id=context.user_data['message_id'],
        text=get_text(lang, 'main_menu'),
        reply_markup=reply_markup
    )
    
    return MAIN_MENU

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'report':
        lang = context.user_data.get('language', 'en')
        context.user_data['report'] = {}
        
        keyboard = [[InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=get_text(lang, 'step1'),
            reply_markup=reply_markup
        )
        
        return REPORT_STEP_1
    elif query.data == 'back_main':
        lang = context.user_data.get('language', 'en')
        keyboard = [[InlineKeyboardButton(get_text(lang, 'report_button'), callback_data='report')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=get_text(lang, 'main_menu'),
            reply_markup=reply_markup
        )
        
        return MAIN_MENU

async def report_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get('language', 'en')
    context.user_data['report']['target'] = update.message.text
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_user.id,
        message_id=context.user_data['message_id'],
        text=get_text(lang, 'step2'),
        reply_markup=reply_markup
    )
    
    return REPORT_STEP_2

async def report_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get('language', 'en')
    context.user_data['report']['reason'] = update.message.text
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_user.id,
        message_id=context.user_data['message_id'],
        text=get_text(lang, 'step3'),
        reply_markup=reply_markup
    )
    
    return REPORT_STEP_3

async def report_step_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get('language', 'en')
    context.user_data['report']['evidence'] = update.message.text
    
    report = context.user_data['report']
    confirm_text = get_text(lang, 'confirm_text', target=report['target'], reason=report['reason'], evidence=report['evidence'])
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'confirm_button'), callback_data='confirm_report'), InlineKeyboardButton(get_text(lang, 'edit_button'), callback_data='edit_report')], [InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_user.id,
        message_id=context.user_data['message_id'],
        text=confirm_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    return REPORT_CONFIRM
  async def confirm_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = context.user_data.get('language', 'en')
    
    if query.data == 'confirm_report':
        report_id = str(uuid.uuid4())[:8].upper()
        context.user_data['report']['id'] = report_id
        context.user_data['report']['timestamp'] = datetime.now().isoformat()
        
        report = context.user_data['report']
        user_info = f"👤 <b>От пользователя:</b>\n├ Имя: {context.user_data['first_name']}\n├ Юзернейм: @{context.user_data['username']}\n└ ID: {context.user_data['user_id']}\n\n"
        
        report_text = f"<b>🚨 НОВАЯ ЖАЛОБА (ID: {report_id})</b>\n\n{user_info}🔗 <b>Канал/Бот:</b> {report['target']}\n⚠️ <b>Причина:</b> {report['reason']}\n📸 <b>Доказательство:</b> {report['evidence']}\n⏰ <b>Время:</b> {report['timestamp']}"
        
        mod_keyboard = [[InlineKeyboardButton('✅ Принять', callback_data=f'accept_{report_id}'), InlineKeyboardButton('❌ Отклонить', callback_data=f'reject_{report_id}')]]
        mod_reply_markup = InlineKeyboardMarkup(mod_keyboard)
        
        await context.bot.send_message(chat_id=MODERATOR_CHAT_ID, text=report_text, reply_markup=mod_reply_markup, parse_mode='HTML')
        
        success_text = get_text(lang, 'success', report_id=report_id)
        keyboard = [[InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=success_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        
        return MAIN_MENU
        
    elif query.data == 'edit_report':
        keyboard = [[InlineKeyboardButton(get_text(lang, 'edit_target'), callback_data='edit_target')], [InlineKeyboardButton(get_text(lang, 'edit_reason'), callback_data='edit_reason')], [InlineKeyboardButton(get_text(lang, 'edit_evidence'), callback_data='edit_evidence')], [InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_to_confirm')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=get_text(lang, 'edit_options'),
            reply_markup=reply_markup
        )
        
        return REPORT_EDIT

async def edit_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    lang = context.user_data.get('language', 'en')
    
    if query.data == 'back_to_confirm':
        report = context.user_data['report']
        confirm_text = get_text(lang, 'confirm_text', target=report['target'], reason=report['reason'], evidence=report['evidence'])
        
        keyboard = [[InlineKeyboardButton(get_text(lang, 'confirm_button'), callback_data='confirm_report'), InlineKeyboardButton(get_text(lang, 'edit_button'), callback_data='edit_report')], [InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=confirm_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        
        return REPORT_CONFIRM
    
    else:
        if query.data == 'edit_target':
            context.user_data['edit_field'] = 'target'
            msg_text = get_text(lang, 'step1')
        elif query.data == 'edit_reason':
            context.user_data['edit_field'] = 'reason'
            msg_text = get_text(lang, 'step2')
        else:
            context.user_data['edit_field'] = 'evidence'
            msg_text = get_text(lang, 'step3')
        
        keyboard = [[InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_to_confirm')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.edit_message_text(
            chat_id=query.from_user.id,
            message_id=context.user_data['message_id'],
            text=msg_text,
            reply_markup=reply_markup
        )
        
        return REPORT_EDIT

async def handle_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get('language', 'en')
    field = context.user_data.get('edit_field', 'target')
    
    context.user_data['report'][field] = update.message.text
    
    report = context.user_data['report']
    confirm_text = get_text(lang, 'confirm_text', target=report['target'], reason=report['reason'], evidence=report['evidence'])
    
    keyboard = [[InlineKeyboardButton(get_text(lang, 'confirm_button'), callback_data='confirm_report'), InlineKeyboardButton(get_text(lang, 'edit_button'), callback_data='edit_report')], [InlineKeyboardButton(get_text(lang, 'back_button'), callback_data='back_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.edit_message_text(
        chat_id=update.effective_user.id,
        message_id=context.user_data['message_id'],
        text=confirm_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    
    return REPORT_CONFIRM

async def moderate_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data.split('_')
    action = data[0]
    report_id = data[1]
    
    message_text = query.message.text
    user_id_line = [line for line in message_text.split('\n') if 'ID:' in line]
    
    if user_id_line:
        try:
            user_id = int(user_id_line[0].split(': ')[1])
            
            if action == 'accept':
                message_text_user = get_text('ru', 'accepted', report_id=report_id)
                await context.bot.send_message(chat_id=user_id, text=message_text_user, parse_mode='HTML')
                status = '✅ ПРИНЯТА'
            else:
                message_text_user = get_text('ru', 'rejected', report_id=report_id)
                await context.bot.send_message(chat_id=user_id, text=message_text_user, parse_mode='HTML')
                status = '❌ ОТКЛОНЕНА'
            
            new_text = query.message.text + f"\n\n🔔 <b>Статус: {status}</b>"
            
            await context.bot.edit_message_text(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=new_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке жалобы: {e}")

async def setup_webhook():
    try:
        webhook_url = f"{EXTERNAL_URL}/bot{BOT_TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"✅ Webhook установлен: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при установке webhook: {e}")
        return False

async def start_app():
    global anti_sleep
    
    anti_sleep = AntySleepSystem(application, interval_minutes=4)
    
    conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            LANGUAGE: [CallbackQueryHandler(select_language, pattern='^lang_')],
            VERIFICATION: [CallbackQueryHandler(verify_user, pattern='^verify_ok$')],
            MAIN_MENU: [CallbackQueryHandler(main_menu, pattern='^report$|^back_main$')],
            REPORT_STEP_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_step_1), CallbackQueryHandler(main_menu, pattern='^back_main$')],
            REPORT_STEP_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_step_2), CallbackQueryHandler(main_menu, pattern='^back_main$')],
            REPORT_STEP_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_step_3), CallbackQueryHandler(main_menu, pattern='^back_main$')],
            REPORT_CONFIRM: [CallbackQueryHandler(confirm_report, pattern='^confirm_report$|^edit_report$|^back_main$')],
            REPORT_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_input), CallbackQueryHandler(edit_report, pattern='^edit_target$|^edit_reason$|^edit_evidence$|^back_to_confirm$')],
        },
        fallbacks=[CommandHandler('start', start)],
    )
    
    application.add_handler(conversation_handler)
    application.add_handler(CallbackQueryHandler(moderate_report, pattern='^(accept|reject)_'))
    
    await application.initialize()
    
    webhook_success = await setup_webhook()
    
    if not webhook_success:
        logger.warning("⚠️ Webhook установка не удалась, но бот всё ещё может работать")
    
    logger.info("✅ Приложение инициализировано")

def run_webhook_server():
    try:
        server = HTTPServer(('0.0.0.0', PORT), WebhookHandler)
        logger.info(f"✅ HTTP сервер запущен на 0.0.0.0:{PORT}")
        logger.info(f"📍 Webhook путь: /bot{BOT_TOKEN}")
        logger.info(f"🔗 Полный URL: {EXTERNAL_URL}/bot{BOT_TOKEN}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Ошибка HTTP сервера: {e}")
        sys.exit(1)

async def main():
    global application
    
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК TELEGRAM BOT - MODERATOR")
    logger.info("=" * 60)
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    await start_app()
    
    anti_sleep_task = asyncio.create_task(anti_sleep.start())
    
    webhook_thread = threading.Thread(target=run_webhook_server, daemon=True)
    webhook_thread.start()
    
    logger.info("=" * 60)
    logger.info("✅ БОТ ПОЛНОСТЬЮ ГОТОВ К РАБОТЕ")
    logger.info("=" * 60)
    logger.info(f"📌 Чат модераторов: https://t.me/gayclubl (ID: {MODERATOR_CHAT_ID})")
    logger.info(f"⏰ Anti-sleep интервал: 4 минуты")
    logger.info(f"🔄 Система пингов активна")
    logger.info("=" * 60)
    
    try:
        await anti_sleep_task
    except KeyboardInterrupt:
        logger.info("⛔ Получен сигнал остановки")
        anti_sleep.stop()
    finally:
        await application.stop()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
      
