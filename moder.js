// src/config.ts
import dotenv from 'dotenv';
dotenv.config();

export const BOT_TOKEN = process.env.BOT_TOKEN || '';
export const MODERATOR_CHAT_ID = -4354663980; // ID чата из ссылки tg://chat?id=4354663980
export const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';

// src/types.ts
export interface ComplaintData {
  userId: number;
  username?: string;
  firstName?: string;
  target: string;       // ссылка или юзернейм
  reason: string;
  proof: string;        // ссылка или скриншот
  step: 'lang' | 'captcha' | 'main' | 'target' | 'reason' | 'proof' | 'confirm';
  language?: 'ru' | 'uk' | 'en';
  tempTarget?: string;
  tempReason?: string;
  tempProof?: string;
}
import TelegramBot from 'node-telegram-bot-api';
import Redis from 'ioredis';
import { BOT_TOKEN, MODERATOR_CHAT_ID, REDIS_URL } from './config';
import { ComplaintData } from './types';

const bot = new TelegramBot(BOT_TOKEN, { polling: true });
const redis = new Redis(REDIS_URL);

// Вспомогательные функции для Redis
const getUserSession = async (userId: number): Promise<ComplaintData | null> => {
  const data = await redis.get(`user:${userId}`);
  return data ? JSON.parse(data) : null;
};

const setUserSession = async (userId: number, data: ComplaintData) => {
  await redis.set(`user:${userId}`, JSON.stringify(data), 'EX', 3600); // 1 час
};

const deleteUserSession = async (userId: number) => {
  await redis.del(`user:${userId}`);
};

// Удаление сообщения
const deleteMessage = (chatId: number, messageId: number) => {
  bot.deleteMessage(chatId, String(messageId)).catch(() => {});
};

// Главное меню
const showMainMenu = async (userId: number, chatId: number) => {
  await setUserSession(userId, {
    userId,
    step: 'main',
    target: '',
    reason: '',
    proof: '',
  });
  await bot.sendMessage(chatId, '🏠 Главное меню:', {
    reply_markup: {
      inline_keyboard: [
        [{ text: '📢 Сообщить о нарушении', callback_data: 'complain' }],
      ],
    },
  });
};

// Старт / начало
bot.onText(/\/start/, async (msg) => {
  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  if (!userId) return;

  await deleteUserSession(userId);

  const langKeyboard = {
    inline_keyboard: [
      [
        { text: '🇷🇺 Русский', callback_data: 'lang_ru' },
        { text: '🇺🇦 Українська', callback_data: 'lang_uk' },
        { text: '🇬🇧 English', callback_data: 'lang_en' },
      ],
    ],
  };

  const sentMsg = await bot.sendMessage(chatId, '🌍 Выберите язык / Оберіть мову / Choose language:', {
    reply_markup: langKeyboard,
  });

  // Сохраняем ID сообщения с выбором языка, чтобы потом удалить
  await redis.set(`lang_msg:${userId}`, sentMsg.message_id, 'EX', 300);
});
// Обработка callback-запросов
bot.on('callback_query', async (query) => {
  const chatId = query.message?.chat.id;
  const userId = query.from.id;
  const messageId = query.message?.message_id;
  const data = query.data || '';

  if (!chatId || !messageId) return;

  // --- ВЫБОР ЯЗЫКА ---
  if (data.startsWith('lang_')) {
    const lang = data.split('_')[1];
    await deleteMessage(chatId, messageId);

    // Удаляем сообщение с выбором языка
    const langMsgId = await redis.get(`lang_msg:${userId}`);
    if (langMsgId) deleteMessage(chatId, Number(langMsgId));

    // Капча "я не робот"
    const captchaMsg = await bot.sendMessage(chatId, '🤖 Подтвердите, что вы не бот:', {
      reply_markup: {
        inline_keyboard: [
          [{ text: '✅ Я не робот', callback_data: 'captcha_ok' }],
        ],
      },
    });
    await redis.set(`captcha_msg:${userId}`, captchaMsg.message_id, 'EX', 300);

    // Сохраняем язык
    const session = await getUserSession(userId);
    if (session) {
      session.language = lang as any;
      await setUserSession(userId, session);
    } else {
      await setUserSession(userId, {
        userId,
        step: 'captcha',
        target: '',
        reason: '',
        proof: '',
        language: lang as any,
      });
    }
    return;
  }

  // --- КАПЧА ---
  if (data === 'captcha_ok') {
    await deleteMessage(chatId, messageId);
    const captchaMsgId = await redis.get(`captcha_msg:${userId}`);
    if (captchaMsgId) deleteMessage(chatId, Number(captchaMsgId));

    await showMainMenu(userId, chatId);
    return;
  }

  // --- ГЛАВНОЕ МЕНЮ: КНОПКА "СООБЩИТЬ" ---
  if (data === 'complain') {
    const session = await getUserSession(userId);
    if (!session) return;

    // Удаляем предыдущее сообщение с меню
    await deleteMessage(chatId, messageId);

    // Просим ввести ссылку на канал/чат или юзернейм бота
    const msg = await bot.sendMessage(chatId, '🔗 Введите ссылку на канал/чат или юзернейм бота-нарушителя:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    session.step = 'target';
    await setUserSession(userId, session);
    return;
  }

  // --- ПОДТВЕРЖДЕНИЕ ЖАЛОБЫ ---
  if (data === 'confirm_complaint') {
    const session = await getUserSession(userId);
    if (!session) return;

    const { target, reason, proof, username, firstName, language } = session;

    // Формируем сообщение для модераторов
    const complaintText = `
🚨 **НОВАЯ ЖАЛОБА** 🚨
👤 **От:** ${username ? `@${username}` : 'Неизвестно'} (ID: ${userId})
📛 **Имя:** ${firstName || 'Не указано'}
🌐 **Язык:** ${language || 'не выбран'}

🔗 **Нарушитель:** ${target}
📝 **Причина:** ${reason}
🖼️ **Доказательство:** ${proof}
    `;

    await bot.sendMessage(MODERATOR_CHAT_ID, complaintText, {
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [{ text: '🔨 Забанить', callback_data: `ban_${target}` }],
        ],
      },
    });

    await bot.sendMessage(chatId, '✅ Ваша жалоба отправлена модераторам! Она будет рассмотрена в ближайшее время.');
    await deleteUserSession(userId);
    await showMainMenu(userId, chatId); // возврат в главное меню
    return;
  }

  // --- ИЗМЕНЕНИЕ ПОЛЯ ЖАЛОБЫ (доп. кнопки) ---
  if (data.startsWith('edit_')) {
    const field = data.split('_')[1];
    const session = await getUserSession(userId);
    if (!session) return;

    await deleteMessage(chatId, messageId);

    let prompt = '';
    if (field === 'target') {
      prompt = '🔗 Введите новую ссылку на канал/чат или юзернейм бота:';
      session.step = 'target';
    } else if (field === 'reason') {
      prompt = '📝 Введите новую причину нарушения:';
      session.step = 'reason';
    } else if (field === 'proof') {
      prompt = '🖼️ Введите новую ссылку или скриншот нарушения:';
      session.step = 'proof';
    }

    await setUserSession(userId, session);
    const msg = await bot.sendMessage(chatId, prompt);
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
  }
});

// Обработка текстовых сообщений (ввод данных)
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;
  const userId = msg.from?.id;
  const text = msg.text;
  if (!userId || !text || msg.from?.is_bot) return;

  // Игнорируем команды (кроме /start, но он уже обработан)
  if (text.startsWith('/')) return;

  const session = await getUserSession(userId);
  if (!session) return;

  // Удаляем предыдущее сообщение бота с запросом ввода
  const inputMsgId = await redis.get(`input_msg:${userId}`);
  if (inputMsgId) {
    deleteMessage(chatId, Number(inputMsgId));
    await redis.del(`input_msg:${userId}`);
  }

  // Удаляем сообщение пользователя (чтобы не засорять чат)
  deleteMessage(chatId, msg.message_id);

  // --- ШАГ: ВВОД ЦЕЛИ ---
  if (session.step === 'target') {
    session.target = text;
    session.step = 'reason';
    await setUserSession(userId, session);

    const msg = await bot.sendMessage(chatId, '📝 Введите причину нарушения:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    return;
  }

  // --- ШАГ: ВВОД ПРИЧИНЫ ---
  if (session.step === 'reason') {
    session.reason = text;
    session.step = 'proof';
    await setUserSession(userId, session);

    const msg = await bot.sendMessage(chatId, '🖼️ Введите ссылку или скриншот нарушения:');
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
    return;
  }

  // --- ШАГ: ВВОД ДОКАЗАТЕЛЬСТВА ---
  if (session.step === 'proof') {
    session.proof = text;
    session.step = 'confirm';
    await setUserSession(userId, session);

    // Показываем предпросмотр жалобы + кнопки подтвердить/изменить
    const preview = `
📋 **Предпросмотр жалобы:**
🔗 Нарушитель: ${session.target}
📝 Причина: ${session.reason}
🖼️ Доказательство: ${session.proof}

Выберите действие:
    `;
    await bot.sendMessage(chatId, preview, {
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Подтвердить', callback_data: 'confirm_complaint' },
            { text: '✏️ Изменить', callback_data: 'edit_menu' },
          ],
        ],
      },
    });
    return;
  }

  // --- ШАГ: ИЗМЕНЕНИЕ ПОЛЯ (если пользователь что-то ввел после выбора edit_menu) ---
  if (session.step === 'edit_target' || session.step === 'edit_reason' || session.step === 'edit_proof') {
    const field = session.step.replace('edit_', '');
    if (field === 'target') session.target = text;
    else if (field === 'reason') session.reason = text;
    else if (field === 'proof') session.proof = text;

    session.step = 'confirm';
    await setUserSession(userId, session);

    // Показываем обновленный предпросмотр
    const preview = `
📋 **Обновленная жалоба:**
🔗 Нарушитель: ${session.target}
📝 Причина: ${session.reason}
🖼️ Доказательство: ${session.proof}

Подтвердите или измените снова:
    `;
    await bot.sendMessage(chatId, preview, {
      parse_mode: 'Markdown',
      reply_markup: {
        inline_keyboard: [
          [
            { text: '✅ Подтвердить', callback_data: 'confirm_complaint' },
            { text: '✏️ Изменить', callback_data: 'edit_menu' },
          ],
        ],
      },
    });
    return;
  }
});

// Обработка кнопки "Изменить" (edit_menu)
bot.on('callback_query', async (query) => {
  const chatId = query.message?.chat.id;
  const userId = query.from.id;
  const messageId = query.message?.message_id;
  const data = query.data || '';

  if (!chatId || !messageId) return;

  if (data === 'edit_menu') {
    await deleteMessage(chatId, messageId);
    await bot.sendMessage(chatId, 'Что вы хотите изменить?', {
      reply_markup: {
        inline_keyboard: [
          [{ text: '🔗 Ссылку на нарушителя', callback_data: 'edit_target' }],
          [{ text: '📝 Причину', callback_data: 'edit_reason' }],
          [{ text: '🖼️ Доказательство', callback_data: 'edit_proof' }],
        ],
      },
    });
  }

  // Обработка выбора поля для изменения
  if (data.startsWith('edit_') && data !== 'edit_menu') {
    const field = data.split('_')[1];
    const session = await getUserSession(userId);
    if (!session) return;

    await deleteMessage(chatId, messageId);
    session.step = `edit_${field}` as any;
    await setUserSession(userId, session);

    let prompt = '';
    if (field === 'target') prompt = '🔗 Введите новую ссылку на канал/чат или юзернейм бота:';
    else if (field === 'reason') prompt = '📝 Введите новую причину нарушения:';
    else if (field === 'proof') prompt = '🖼️ Введите новую ссылку или скриншот нарушения:';

    const msg = await bot.sendMessage(chatId, prompt);
    await redis.set(`input_msg:${userId}`, msg.message_id, 'EX', 300);
  }
});

console.log('🚀 Бот запущен!');
