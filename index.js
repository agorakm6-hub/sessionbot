const express = require('express');
const { Telegraf, Markup } = require('telegraf');
const axios = require('axios');
require('dotenv').config();

// --- ИНИЦИАЛИЗАЦИЯ ---
const app = express();
const PORT = process.env.PORT || 3000;
const WEBHOOK_PATH = process.env.WEBHOOK_SECRET || '/webhook';

// Бот
const bot = new Telegraf(process.env.BOT_TOKEN);

// --- OSINT ФУНКЦИИ ---

// 1. Поиск username в соцсетях (Sherlock через внешний API)
async function searchUsername(username) {
    try {
        const response = await axios.get(`https://api.sherlock-project.onrender.com/api/v1/search?q=${username}`, {
            timeout: 30000
        });
        return response.data;
    } catch (error) {
        console.error('Sherlock API error:', error.message);
        return null;
    }
}

// 2. Получение информации о пользователе/чате
async function getChatInfo(ctx, chatId) {
    try {
        const chat = await ctx.telegram.getChat(chatId);
        const memberCount = chat.member_count || 'Неизвестно';
        const admins = await ctx.telegram.getChatAdministrators(chatId);
        const adminsList = admins.map(a => `@${a.user.username || a.user.first_name}`).join(', ');
        
        return {
            id: chat.id,
            type: chat.type,
            title: chat.title || chat.first_name,
            username: chat.username || 'Нет',
            description: chat.description || 'Нет',
            memberCount: memberCount,
            inviteLink: chat.invite_link || 'Нет',
            admins: adminsList || 'Нет'
        };
    } catch (error) {
        console.error('GetChatInfo error:', error.message);
        return null;
    }
}

// 3. Проверка активности пользователя
async function getUserActivity(ctx, userId) {
    try {
        const user = await ctx.telegram.getChatMember(userId, userId);
        return {
            status: user.status,
            isBot: user.user.is_bot,
            firstName: user.user.first_name,
            lastName: user.user.last_name || '',
            username: user.user.username || 'Нет'
        };
    } catch (error) {
        console.error('GetUserActivity error:', error.message);
        return null;
    }
}

// --- КОМАНДЫ БОТА ---

// Старт с приветствием и списком команд
bot.start(async (ctx) => {
    const welcomeMessage = 
        `🔍 *Добро пожаловать в OSINT Telegram Bot!*\n\n` +
        `Я помогу тебе собирать информацию из открытых источников и анализировать данные в Telegram.\n\n` +
        `📋 *Доступные команды:*\n` +
        `🔎 /search <username> - Поиск профиля в 170+ соцсетях\n` +
        `📊 /info <chat_id> - Информация о чате или пользователе\n` +
        `👤 /who <user_id> - Данные о пользователе\n` +
        `❓ /help - Подробная справка\n\n` +
        `💡 *Примеры:*\n` +
        `/search elonmusk\n` +
        `/info -100123456789\n` +
        `/who 123456789\n\n` +
        `_Бот использует открытые API и данные Telegram._`;

    const keyboard = Markup.inlineKeyboard([
        [
            Markup.button.callback('🔎 Поиск', 'help_search'),
            Markup.button.callback('📊 Инфо', 'help_info')
        ],
        [
            Markup.button.callback('👤 Пользователь', 'help_who'),
            Markup.button.callback('❓ Помощь', 'help_help')
        ]
    ]);

    await ctx.reply(welcomeMessage, { 
        parse_mode: 'Markdown',
        ...keyboard
    });
});

// Обработка кнопок
bot.action('help_search', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `🔎 *Команда /search*\n\n` +
        `Ищет username на 170+ популярных платформах.\n\n` +
        `*Пример:*\n` +
        `/search elonmusk\n\n` +
        `*Результат:* список сайтов, где найден профиль.\n\n` +
        `⏱ *Время ожидания:* до 30 секунд`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_info', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `📊 *Команда /info*\n\n` +
        `Показывает подробную информацию о чате или пользователе.\n\n` +
        `*Что выводит:*\n` +
        `• ID чата\n` +
        `• Тип (группа/канал/личка)\n` +
        `• Название\n` +
        `• Количество участников\n` +
        `• Список администраторов\n` +
        `• Ссылка-приглашение\n\n` +
        `*Пример:*\n` +
        `/info -100123456789`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_who', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `👤 *Команда /who*\n\n` +
        `Показывает информацию о пользователе Telegram.\n\n` +
        `*Что выводит:*\n` +
        `• Имя и фамилия\n` +
        `• Username\n` +
        `• ID\n` +
        `• Статус (админ/участник/забанен)\n` +
        `• Является ли ботом\n\n` +
        `*Пример:*\n` +
        `/who 123456789\n\n` +
        `⚠️ *Важно:* бот должен быть в общем чате с пользователем.`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_help', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `❓ *Полная справка*\n\n` +
        `*Основные команды:*\n` +
        `/start - Главное меню\n` +
        `/help - Эта справка\n\n` +
        `*OSINT команды:*\n` +
        `/search <username> - Поиск в соцсетях\n` +
        `/info <chat_id> - Информация о чате\n` +
        `/who <user_id> - Данные о пользователе\n\n` +
        `*Как получить ID:*\n` +
        `1. Перешли сообщение боту @userinfobot\n` +
        `2. Или используй /info в группе, где бот админ\n\n` +
        `*Ограничения:*\n` +
        `• Бот работает только с открытыми данными\n` +
        `• Для /who и /info бот должен быть в чате`,
        { parse_mode: 'Markdown' }
    );
});

// Помощь
bot.help(async (ctx) => {
    await ctx.reply(
        `📖 *Подробная инструкция*\n\n` +
        `1. /search username - Находит профили в 170+ соцсетях\n` +
        `2. /info -123456789 - Показывает данные о чате\n` +
        `3. /who 123456789 - Информация о пользователе\n\n` +
        `*Как получить ID чата:*\n` +
        `Добавь бота @userinfobot и перешли любое сообщение\n\n` +
        `*Примеры:*\n` +
        `/search elonmusk\n` +
        `/info -100123456789\n` +
        `/who 123456789\n\n` +
        `🔄 Для меню используй /start`,
        { parse_mode: 'Markdown' }
    );
});

// Поиск по username (Sherlock)
bot.command('search', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи username для поиска*\n\n` +
            `*Пример:* /search elonmusk\n` +
            `*Помощь:* /help search`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const username = args[1];
    const searchMsg = await ctx.reply(
        `🔍 *Ищу ${username}...*\n` +
        `⏱ Это может занять до 30 секунд`,
        { parse_mode: 'Markdown' }
    );

    try {
        const results = await searchUsername(username);
        
        if (!results || !results.sites) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                searchMsg.message_id,
                null,
                `❌ *Не удалось выполнить поиск*\n\n` +
                `Сервис временно недоступен. Попробуй позже.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        const foundSites = Object.entries(results.sites)
            .filter(([site, data]) => data.found)
            .slice(0, 20);

        if (foundSites.length === 0) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                searchMsg.message_id,
                null,
                `❌ *Не найдено профилей для ${username}*\n\n` +
                `Проверь правильность написания или попробуй другой username.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `✅ *Найдено ${foundSites.length} профилей для ${username}:*\n\n`;
        foundSites.forEach(([site, data]) => {
            message += `• [${site}](${data.url})\n`;
        });
        message += `\n_🔗 Все ссылки ведут на публичные профили_`;

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            searchMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown', disable_web_page_preview: true }
        );

        // Добавляем кнопку для повторного поиска
        await ctx.reply(
            `🔎 *Хочешь проверить другой username?*`,
            {
                parse_mode: 'Markdown',
                ...Markup.inlineKeyboard([
                    [Markup.button.switchToCurrentChat('🔍 Начать поиск', '/search ')]
                ])
            }
        );

    } catch (error) {
        console.error('Search error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            searchMsg.message_id,
            null,
            `❌ *Ошибка при поиске*\n\n` +
            `Попробуй позже или используй другой username.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Информация о чате
bot.command('info', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи ID чата*\n\n` +
            `*Пример:* /info -100123456789\n` +
            `*Как получить ID:* /help info`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const chatId = parseInt(args[1]);
    if (isNaN(chatId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const infoMsg = await ctx.reply('📊 *Получаю информацию...*', { parse_mode: 'Markdown' });

    try {
        const info = await getChatInfo(ctx, chatId);
        if (!info) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                infoMsg.message_id,
                null,
                `❌ *Не удалось получить информацию*\n\n` +
                `Проверь ID и убедись, что бот является администратором чата.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `📋 *Информация о чате*\n\n`;
        message += `🆔 ID: \`${info.id}\`\n`;
        message += `📝 Тип: *${info.type}*\n`;
        message += `📛 Название: *${info.title}*\n`;
        message += `👤 Username: ${info.username ? '@' + info.username : 'Нет'}\n`;
        message += `📖 Описание: ${info.description || 'Нет'}\n`;
        message += `👥 Участников: *${info.memberCount}*\n`;
        message += `🔗 Ссылка: ${info.inviteLink || 'Нет'}\n`;
        message += `👑 Админы: ${info.admins || 'Нет'}`;

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            infoMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('Info error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            infoMsg.message_id,
            null,
            `❌ *Ошибка при получении информации*\n\n` +
            `Убедись, что бот является администратором чата.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Информация о пользователе
bot.command('who', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи ID пользователя*\n\n` +
            `*Пример:* /who 123456789\n` +
            `*Как получить ID:* /help who`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const userId = parseInt(args[1]);
    if (isNaN(userId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const whoMsg = await ctx.reply('👤 *Проверяю пользователя...*', { parse_mode: 'Markdown' });

    try {
        const info = await getUserActivity(ctx, userId);
        if (!info) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                whoMsg.message_id,
                null,
                `❌ *Не удалось получить информацию*\n\n` +
                `Бот должен быть в общем чате с пользователем.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `👤 *Информация о пользователе*\n\n`;
        message += `🆔 ID: \`${userId}\`\n`;
        message += `📛 Имя: *${info.firstName}*\n`;
        if (info.lastName) message += `📛 Фамилия: *${info.lastName}*\n`;
        message += `👤 Username: ${info.username !== 'Нет' ? '@' + info.username : 'Нет'}\n`;
        message += `🤖 Бот: ${info.isBot ? '✅ Да' : '❌ Нет'}\n`;
        message += `📊 Статус: *${info.status}*`;

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            whoMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('Who error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            whoMsg.message_id,
            null,
            `❌ *Ошибка при получении информации*\n\n` +
            `Убедись, что пользователь существует и бот может его видеть.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Обработка текстовых сообщений (не команд)
bot.on('text', async (ctx) => {
    const text = ctx.message.text;
    
    // Если сообщение похоже на username (начинается с @)
    if (text.startsWith('@') && text.length > 1) {
        const username = text.substring(1);
        await ctx.reply(
            `🔍 *Найти ${username}?*\n\n` +
            `Используй команду:\n` +
            `/search ${username}\n\n` +
            `Или выбери действие:`,
            {
                parse_mode: 'Markdown',
                ...Markup.inlineKeyboard([
                    [
                        Markup.button.callback('🔎 Поиск', `search_${username}`),
                        Markup.button.callback('📊 Инфо о чате', `info_${username}`)
                    ]
                ])
            }
        );
        return;
    }

    // Если сообщение - ссылка на канал/чат
    if (text.includes('t.me/')) {
        await ctx.reply(
            `🔗 *Обнаружена ссылка на Telegram*\n\n` +
            `Чтобы получить информацию о чате, используй:\n` +
            `/info [ID чата]\n\n` +
            `*Как узнать ID чата:*\n` +
            `1. Добавь бота @userinfobot\n` +
            `2. Перешли любое сообщение из чата\n` +
            `3. Получи ID`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    // Помощь для новых пользователей
    await ctx.reply(
        `❓ *Неизвестная команда*\n\n` +
        `Используй /start для просмотра всех доступных команд.\n\n` +
        `*Быстрый старт:*\n` +
        `🔎 /search username - поиск в соцсетях\n` +
        `📊 /info chat_id - информация о чате\n` +
        `👤 /who user_id - информация о пользователе`,
        { parse_mode: 'Markdown' }
    );
});

// --- НАСТРОЙКА WEBHOOK ---

// Express для Webhook
app.use(express.json());
app.use(bot.webhookCallback(WEBHOOK_PATH));

// Корневой путь (для проверки работы)
app.get('/', (req, res) => {
    res.send('🤖 OSINT Telegram Bot is running!');
});

// Запуск сервера
app.listen(PORT, async () => {
    console.log(`🚀 Server running on port ${PORT}`);
    console.log(`🤖 Bot name: ${bot.botInfo?.username || 'unknown'}`);
    console.log(`🔗 Webhook path: ${WEBHOOK_PATH}`);
});

// --- ОБРАБОТКА ОШИБОК ---
process.on('uncaughtException', (error) => {
    console.error('Uncaught Exception:', error);
});

process.on('unhandledRejection', (reason, promise) => {
    console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});
