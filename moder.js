const TelegramBot = require('node-telegram-bot-api');
const Redis = require('ioredis');
require('dotenv').config();

const BOT_TOKEN = process.env.BOT_TOKEN;
const MODERATOR_CHAT_ID = -4354663980;
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN не найден!');
  process.exit(1);
}

const bot = new TelegramBot(BOT_TOKEN, { polling: true });
const redis = new Redis(REDIS_URL);

// ===== ФУНКЦИИ РАБОТЫ С СЕССИЯМИ =====
const getSession = async (userId) => {
  const data = await redis.get(`user:${userId}`);
  return data ? JSON.parse(data) : null;
};

const setSession = async (userId, data) => {
  await redis.set(`user:${userId}`, JSON.stringify(data), 'EX', 3600);
};

const deleteSession = async (userId) => {
  await redis.del(`user:${userId}`);
};

const deleteMsg = (chatId, msgId) => {
  bot.deleteMessage(chatId, String(msgId)).catch(() => {});
};

// ===== ГЛАВНОЕ МЕНЮ =====
const showMainMenu = async (userId, chatId) => {
  await setSession(userId, { userId, step: 'main' });
  await bot.sendMessage(chatId, '🏠 Главное меню:', {
    reply_markup: {
      inline_keyboard: [
        [{ text: '📢 Сообщить о нарушении', callback_data: 'complain' }]
      ]
    }
  });
};

// ===== СТАРТ =====
bot.onText(/\/start/, async (msg) => {
  const chatId = msg.chat.id;
  const userId = msg.from.id;

  await deleteSession(userId);

  const sent = await bot.sendMessage(chatId, '🌍 Выберите язык:', {
    reply_markup: {
      inline_keyboard: [
        [
          { text: '🇷🇺 Русский', callback_data: 'lang_ru' },
          { text: '🇺🇦 Українська', callback_data: 'lang_uk' },
          { text: '🇬🇧 English', callback_data: 'lang_en' }
        ]
      ]
    }
  });

  await redis.set(`lang_msg:${userId}`, sent.message_id, 'EX', 300);
});

// ===== CALLBACK КНОПКИ =====
bot.on('callback_query', async (query) => {
  const chatId = query.message.chat.id;
  const userId = query.from.id;
  const msgId = query.message.message_id;
  const data = query.data;

  // ВЫБОР ЯЗЫКА
  if (data.startsWith('lang_')) {
    const lang = data.split('_')[1];
    await deleteMsg(chatId, msgId);

    const langMsgId = await redis.get(`lang_msg:${userId}`);
    if (langMsgId) deleteMsg(chatId, Number(langMsgId));

    const captcha = await bot.sendMessage(chatId, '🤖 Подтвердите, что вы не бот:', {
      reply_markup: {
        inline_keyboard: [
          [{ text: '✅ Я не робот', callback_data: 'captcha_ok' }]
        ]
      }
    });

    await redis.set(`captcha_msg:${userId}`, captcha.message_id, 'EX', 300);
    await setSession(userId, { userId, step: 'captcha', language: lang });
    return;
  }

  // КАПЧА
  if (data === 'captcha_ok') {
    await deleteMsg(chatId, msgId);
    const captchaMsgId = await redis.get(`captcha_msg:${userId}`);
    if (captchaMsgId) deleteMsg(chatId, Number(captchaMsgId));

    await showMainMenu(userId, chatId);
    return;
  }

  // КНОПКА "СООБЩИТЬ"
  if (data === 'complain') {
    await deleteMsg(chatId, msgId);
    const session = await getSession(userId) || { userId, step: 'target' };
    session.step = 'target';
    await setSession(userId, session);

    const msg = await bot.sendMessage(chatId, '🔗 Введите ссылку на канал/чат или юзернейм бота:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    return;
  }

  // ПОДТВЕРДИТЬ ЖАЛОБУ
  if (data === 'confirm_complaint') {
    const session = await getSession(userId);
    if (!session || !session.target || !session.reason || !session.proof) {
      await bot.sendMessage(chatId, '❌ Данные неполные, начните заново /start');
      return;
    }

    const user = query.from;
    const complaint = `
🚨 **НОВАЯ ЖАЛОБА** 🚨
👤 От: @${user.username || 'неизвестно'} (ID: ${userId})
📛 Имя: ${user.first_name || 'не указано'}

🔗 Нарушитель: ${session.target}
📝 Причина: ${session.reason}
🖼️ Доказательство: ${session.proof}
    `;

    await bot.sendMessage(MODERATOR_CHAT_ID, complaint, { parse_mode: 'Markdown' });
    await bot.sendMessage(chatId, '✅ Жалоба отправлена модераторам!');
    await deleteSession(userId);
    await showMainMenu(userId, chatId);
    return;
  }

  // КНОПКА "ИЗМЕНИТЬ"
  if (data === 'edit_menu') {
    await deleteMsg(chatId, msgId);
    await bot.sendMessage(chatId, 'Что изменить?', {
      reply_markup: {
        inline_keyboard: [
          [{ text: '🔗 Ссылку', callback_data: 'edit_target' }],
          [{ text: '📝 Причину', callback_data: 'edit_reason' }],
          [{ text: '🖼️ Доказательство', callback_data: 'edit_proof' }]
        ]
      }
    });
    return;
  }

  // ВЫБОР ПОЛЯ ДЛЯ ИЗМЕНЕНИЯ
  if (data.startsWith('edit_') && data !== 'edit_menu') {
    const field = data.split('_')[1];
    const session = await getSession(userId);
    if (!session) return;

    await deleteMsg(chatId, msgId);
    session.step = `edit_${field}`;
    await setSession(userId, session);

    const prompts = {
      target: '🔗 Введите новую ссылку:',
      reason: '📝 Введите новую причину:',
      proof: '🖼️ Введите новое доказательство:'
    };

    const msg = await bot.sendMessage(chatId, prompts[field]);
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
  }
});

// ===== ТЕКСТОВЫЕ СООБЩЕНИЯ =====
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;
  const userId = msg.from.id;
  const text = msg.text;

  if (!text || msg.from.is_bot || text.startsWith('/')) return;

  const session = await getSession(userId);
  if (!session) return;

  const inputMsgId = await redis.get(`input_msg:${userId}`);
  if (inputMsgId) {
    deleteMsg(chatId, Number(inputMsgId));
    await redis.del(`input_msg:${userId}`);
  }

  deleteMsg(chatId, msg.message_id);

  // ВВОД ЦЕЛИ
  if (session.step === 'target') {
    session.target = text;
    session.step = 'reason';
    await setSession(userId, session);

    const msg = await bot.sendMessage(chatId, '📝 Введите причину нарушения:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    return;
  }

  // ВВОД ПРИЧИНЫ
  if (session.step === 'reason') {
    session.reason = text;
    session.step = 'proof';
    await setSession(userId, session);

    const msg = await bot.sendMessage(chatId, '🖼️ Введите ссылку или скриншот нарушения:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    return;
  }

  // ВВОД ДОКАЗАТЕЛЬСТВА
  if (session.step === 'proof') {
    session.proof = text;
    session.step = 'confirm';
    await setSession(userId, session);

    await bot.sendMessage(chatId, `
📋 **Предпросмотр:**
🔗 Нарушитель: ${session.target}
📝 Причина: ${session.reason}
🖼️ Доказательство: ${session.proof}
    `, {
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Подтвердить', callback_data: 'confirm_complaint' },
            { text: '✏️ Изменить', callback_data: 'edit_menu' }
          ]
        ]
      }
    });
    return;
  }

  // РЕДАКТИРОВАНИЕ ПОЛЕЙ
  if (['edit_target', 'edit_reason', 'edit_proof'].includes(session.step)) {
    const field = session.step.replace('edit_', '');
    session[field] = text;
    session.step = 'confirm';
    await setSession(userId, session);

    await bot.sendMessage(chatId, `
📋 **Обновлено:**
🔗 Нарушитель: ${session.target}
📝 Причина: ${session.reason}
🖼️ Доказательство: ${session.proof}
    `, {
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Подтвердить', callback_data: 'confirm_complaint' },
            { text: '✏️ Изменить', callback_data: 'edit_menu' }
          ]
        ]
      }
    });
  }
});

console.log('✅ Бот запущен!');
