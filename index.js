const { Telegraf, Markup } = require('telegraf');
const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions');
const axios = require('axios');
const express = require('express');

// --- ПРОВЕРКА ПЕРЕМЕННЫХ ---
if (!process.env.BOT_TOKEN) {
    console.error('❌ BOT_TOKEN не найден');
    process.exit(1);
}
if (!process.env.TG_API_ID || !process.env.TG_API_HASH) {
    console.error('❌ TG_API_ID или TG_API_HASH не найдены');
    process.exit(1);
}
if (!process.env.SESSION_STRING) {
    console.error('❌ SESSION_STRING не найдена');
    process.exit(1);
}

console.log('✅ Переменные окружения загружены');

// --- ИНИЦИАЛИЗАЦИЯ БОТА ---
const bot = new Telegraf(process.env.BOT_TOKEN);

// --- ПОДКЛЮЧЕНИЕ К АККАУНТУ (MTProto) ---
const client = new TelegramClient(
    new StringSession(process.env.SESSION_STRING),
    parseInt(process.env.TG_API_ID),
    process.env.TG_API_HASH,
    {
        connectionRetries: 5,
        useWSS: false,
    }
);

// --- ЗАПУСК КЛИЕНТА ---
let clientReady = false;

(async () => {
    try {
        console.log('🔌 Подключаюсь к Telegram MTProto...');
        await client.start();
        const me = await client.getMe();
        clientReady = true;
        console.log(`✅ Авторизован как: ${me.firstName} (@${me.username || 'нет username'})`);
        console.log(`🆔 ID: ${me.id}`);
    } catch (error) {
        console.error('❌ Ошибка авторизации клиента:', error.message);
    }
})();

// --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ С ПРОВЕРКОЙ КЛИЕНТА ---

async function ensureClient() {
    if (!clientReady) {
        throw new Error('Клиент еще не готов');
    }
    if (!client.connected) {
        await client.connect();
    }
}

async function searchSherlock(username) {
    try {
        const response = await axios.get(
            `https://api.sherlock-project.onrender.com/api/v1/search?q=${username}`,
            { timeout: 30000 }
        );
        return response.data;
    } catch (error) {
        console.error('Sherlock API error:', error.message);
        return null;
    }
}

async function getFullUserInfo(username) {
    try {
        await ensureClient();
        
        const searchResult = await client.invoke({
            _: 'contacts.search',
            q: username,
            limit: 1
        });

        if (searchResult.users.length === 0) {
            return null;
        }

        const user = searchResult.users[0];
        
        const fullUser = await client.invoke({
            _: 'users.getFullUser',
            id: user.id
        });

        return {
            id: user.id,
            firstName: user.firstName || '',
            lastName: user.lastName || '',
            username: user.username || '',
            phone: user.phone || 'Скрыт',
            isBot: user.bot || false,
            verified: user.verified || false,
            restricted: user.restricted || false,
            status: user.status || null,
            about: fullUser.fullUser?.about || '',
            commonChatsCount: fullUser.fullUser?.commonChatsCount || 0,
            usernames: fullUser.fullUser?.usernames || [],
            photoId: fullUser.fullUser?.photo?.photoId || null
        };
    } catch (error) {
        console.error('GetFullUserInfo error:', error.message);
        return null;
    }
}

async function getChatInfo(chatId) {
    try {
        await ensureClient();
        
        const chat = await client.invoke({
            _: 'channels.getFullChannel',
            channel: chatId
        });

        return {
            id: chat.fullChat.id,
            title: chat.fullChat.title || '',
            username: chat.fullChat.username || '',
            about: chat.fullChat.about || '',
            participantsCount: chat.fullChat.participantsCount || 0,
            adminsCount: chat.fullChat.adminsCount || 0,
            kickedCount: chat.fullChat.kickedCount || 0,
            bannedCount: chat.fullChat.bannedCount || 0,
            onlineCount: chat.fullChat.onlineCount || 0,
            isPublic: chat.fullChat.username ? true : false,
            inviteLink: chat.fullChat.inviteLink || '',
            slowMode: chat.fullChat.slowmodeSeconds || 0,
            hiddenPrehistory: chat.fullChat.hiddenPrehistory || false
        };
    } catch (error) {
        console.error('GetChatInfo error:', error.message);
        return null;
    }
}

async function getChatMembers(chatId, limit = 100) {
    try {
        await ensureClient();
        
        const participants = await client.invoke({
            _: 'channels.getParticipants',
            channel: chatId,
            filter: { _: 'channelParticipantsRecent' },
            offset: 0,
            limit: limit,
            hash: 0
        });

        return participants.users.map(user => ({
            id: user.id,
            firstName: user.firstName || '',
            lastName: user.lastName || '',
            username: user.username || '',
            phone: user.phone || 'Скрыт',
            isBot: user.bot || false
        }));
    } catch (error) {
        console.error('GetChatMembers error:', error.message);
        return null;
    }
}

async function getNearbyUsers(lat, lon, limit = 20) {
    try {
        await ensureClient();
        
        const result = await client.invoke({
            _: 'contacts.getLocated',
            geoPoint: {
                _: 'inputGeoPoint',
                lat: lat,
                long: lon
            },
            limit: limit
        });

        return result.users.map(user => ({
            id: user.id,
            firstName: user.firstName || '',
            lastName: user.lastName || '',
            username: user.username || '',
            phone: user.phone || 'Скрыт',
            isBot: user.bot || false
        }));
    } catch (error) {
        console.error('GetNearbyUsers error:', error.message);
        return null;
    }
}

// --- КОМАНДЫ БОТА ---

bot.start(async (ctx) => {
    const welcomeMessage = 
        `🔍 *OSINT Telegram Bot*\n\n` +
        `📋 *Команды:*\n` +
        `🔎 /search <username> - Поиск в 170+ соцсетях\n` +
        `👤 /whois <username> - Полная инфо о пользователе\n` +
        `📜 /history <username> - История смены username\n` +
        `📊 /chat <chat_id> - Информация о чате\n` +
        `👥 /members <chat_id> - Экспорт участников\n` +
        `📍 /geo - Найти людей рядом\n` +
        `❓ /help - Подробная справка\n\n` +
        `💡 *Примеры:*\n` +
        `/whois durov\n` +
        `/chat -100123456789`;

    const keyboard = Markup.inlineKeyboard([
        [
            Markup.button.callback('👤 WhoIs', 'help_whois'),
            Markup.button.callback('📜 История', 'help_history')
        ],
        [
            Markup.button.callback('📊 Чат', 'help_chat'),
            Markup.button.callback('👥 Участники', 'help_members')
        ],
        [
            Markup.button.callback('📍 Geo', 'help_geo'),
            Markup.button.callback('🔎 Поиск', 'help_search')
        ]
    ]);

    await ctx.reply(welcomeMessage, {
        parse_mode: 'Markdown',
        ...keyboard
    });
});

// Обработка кнопок
bot.action('help_whois', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`👤 *Команда /whois*\n\n*Пример:* /whois durov`, { parse_mode: 'Markdown' });
});

bot.action('help_history', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`📜 *Команда /history*\n\n*Пример:* /history durov`, { parse_mode: 'Markdown' });
});

bot.action('help_chat', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`📊 *Команда /chat*\n\n*Пример:* /chat -100123456789`, { parse_mode: 'Markdown' });
});

bot.action('help_members', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`👥 *Команда /members*\n\n*Пример:* /members -100123456789 50`, { parse_mode: 'Markdown' });
});

bot.action('help_geo', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`📍 *Команда /geo*\n\nОтправь геопозицию`, { parse_mode: 'Markdown' });
});

bot.action('help_search', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(`🔎 *Команда /search*\n\n*Пример:* /search elonmusk`, { parse_mode: 'Markdown' });
});

bot.help(async (ctx) => {
    await ctx.reply(
        `📖 *Команды:*\n` +
        `/search <username> - Поиск в соцсетях\n` +
        `/whois <username> - Инфо о пользователе\n` +
        `/history <username> - История username\n` +
        `/chat <chat_id> - Инфо о чате\n` +
        `/members <chat_id> - Экспорт участников\n` +
        `/geo - Найти людей рядом`,
        { parse_mode: 'Markdown' }
    );
});
// --- ВСЕ КОМАНДЫ ---

bot.command('search', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(`❌ Укажи username: /search elonmusk`, { parse_mode: 'Markdown' });
        return;
    }

    const username = args[1];
    const searchMsg = await ctx.reply(`🔍 Ищу ${username}...`, { parse_mode: 'Markdown' });

    try {
        const results = await searchSherlock(username);
        
        if (!results || !results.sites) {
            await ctx.telegram.editMessageText(ctx.chat.id, searchMsg.message_id, null, '❌ Сервис недоступен');
            return;
        }

        const foundSites = Object.entries(results.sites)
            .filter(([_, data]) => data.found)
            .slice(0, 20);

        if (foundSites.length === 0) {
            await ctx.telegram.editMessageText(ctx.chat.id, searchMsg.message_id, null, `❌ Не найдено профилей для ${username}`);
            return;
        }

        let message = `✅ *Найдено ${foundSites.length} профилей:*\n\n`;
        foundSites.forEach(([site, data]) => {
            message += `• [${site}](${data.url})\n`;
        });

        await ctx.telegram.editMessageText(ctx.chat.id, searchMsg.message_id, null, message, {
            parse_mode: 'Markdown',
            disable_web_page_preview: true
        });

    } catch (error) {
        await ctx.telegram.editMessageText(ctx.chat.id, searchMsg.message_id, null, '❌ Ошибка поиска');
    }
});

bot.command('whois', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(`❌ Укажи username: /whois durov`, { parse_mode: 'Markdown' });
        return;
    }

    const username = args[1].replace('@', '');
    const whoMsg = await ctx.reply(`👤 Ищу @${username}...`, { parse_mode: 'Markdown' });

    try {
        const userInfo = await getFullUserInfo(username);
        
        if (!userInfo) {
            await ctx.telegram.editMessageText(ctx.chat.id, whoMsg.message_id, null, `❌ Пользователь @${username} не найден`);
            return;
        }

        let message = `👤 *Информация о пользователе*\n\n`;
        message += `🆔 ID: \`${userInfo.id}\`\n`;
        message += `📛 Имя: ${userInfo.firstName} ${userInfo.lastName || ''}\n`;
        message += `👤 Username: ${userInfo.username ? '@' + userInfo.username : 'Нет'}\n`;
        message += `📱 Телефон: ${userInfo.phone || 'Скрыт'}\n`;
        message += `🤖 Бот: ${userInfo.isBot ? '✅ Да' : '❌ Нет'}\n`;
        message += `✅ Верифицирован: ${userInfo.verified ? 'Да' : 'Нет'}\n`;
        message += `📊 Статус: ${userInfo.status ? '🟢 Онлайн' : '⚫ Оффлайн'}\n`;
        message += `📝 Описание: ${userInfo.about || 'Нет'}\n`;
        message += `👥 Общих чатов: ${userInfo.commonChatsCount || 0}\n`;
        
        if (userInfo.usernames && userInfo.usernames.length > 0) {
            message += `\n📜 *История username:*\n`;
            userInfo.usernames.forEach(u => {
                message += `• @${u.username} ${u.active ? '✅' : '❌'}\n`;
            });
        }

        await ctx.telegram.editMessageText(ctx.chat.id, whoMsg.message_id, null, message, { parse_mode: 'Markdown' });

    } catch (error) {
        await ctx.telegram.editMessageText(ctx.chat.id, whoMsg.message_id, null, '❌ Ошибка');
    }
});

bot.command('history', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(`❌ Укажи username: /history durov`, { parse_mode: 'Markdown' });
        return;
    }

    const username = args[1].replace('@', '');
    const histMsg = await ctx.reply(`📜 Получаю историю @${username}...`, { parse_mode: 'Markdown' });

    try {
        const userInfo = await getFullUserInfo(username);
        
        if (!userInfo) {
            await ctx.telegram.editMessageText(ctx.chat.id, histMsg.message_id, null, `❌ Пользователь @${username} не найден`);
            return;
        }

        if (!userInfo.usernames || userInfo.usernames.length === 0) {
            await ctx.telegram.editMessageText(ctx.chat.id, histMsg.message_id, null, `📜 Нет истории для @${username}`);
            return;
        }

        let message = `📜 *История username для @${username}:*\n\n`;
        userInfo.usernames.forEach(u => {
            message += `• @${u.username} - ${u.active ? '✅ активен' : '❌ неактивен'}\n`;
        });

        await ctx.telegram.editMessageText(ctx.chat.id, histMsg.message_id, null, message, { parse_mode: 'Markdown' });

    } catch (error) {
        await ctx.telegram.editMessageText(ctx.chat.id, histMsg.message_id, null, '❌ Ошибка');
    }
});

bot.command('chat', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(`❌ Укажи ID: /chat -100123456789`, { parse_mode: 'Markdown' });
        return;
    }

    const chatId = parseInt(args[1]);
    if (isNaN(chatId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const chatMsg = await ctx.reply(`📊 Получаю информацию...`, { parse_mode: 'Markdown' });

    try {
        const info = await getChatInfo(chatId);
        
        if (!info) {
            await ctx.telegram.editMessageText(ctx.chat.id, chatMsg.message_id, null, '❌ Бот должен быть админом');
            return;
        }

        let message = `📊 *Информация о чате*\n\n`;
        message += `🆔 ID: \`${info.id}\`\n`;
        message += `📛 Название: *${info.title}*\n`;
        message += `👤 Username: ${info.username ? '@' + info.username : 'Нет'}\n`;
        message += `👥 Участников: *${info.participantsCount}*\n`;
        message += `🟢 Онлайн: *${info.onlineCount || 0}*\n`;
        message += `👑 Админов: *${info.adminsCount || 0}*\n`;
        message += `🔒 Публичный: ${info.isPublic ? '✅ Да' : '❌ Нет'}`;

        await ctx.telegram.editMessageText(ctx.chat.id, chatMsg.message_id, null, message, { parse_mode: 'Markdown' });

    } catch (error) {
        await ctx.telegram.editMessageText(ctx.chat.id, chatMsg.message_id, null, '❌ Ошибка');
    }
});

bot.command('members', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(`❌ Укажи ID: /members -100123456789 50`, { parse_mode: 'Markdown' });
        return;
    }

    const chatId = parseInt(args[1]);
    const limit = args[2] ? Math.min(parseInt(args[2]), 5000) : 100;

    if (isNaN(chatId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const membersMsg = await ctx.reply(`👥 Экспортирую ${limit} участников...`, { parse_mode: 'Markdown' });

    try {
        const members = await getChatMembers(chatId, limit);
        
        if (!members || members.length === 0) {
            await ctx.telegram.editMessageText(ctx.chat.id, membersMsg.message_id, null, '❌ Бот должен быть админом');
            return;
        }

        let csv = `ID,Имя,Фамилия,Username,Телефон,Бот\n`;
        members.forEach(m => {
            csv += `${m.id},"${m.firstName}","${m.lastName}","${m.username}","${m.phone}",${m.isBot}\n`;
        });

        await ctx.replyWithDocument({
            source: Buffer.from(csv, 'utf-8'),
            filename: `members_${chatId}_${Date.now()}.csv`
        });

        await ctx.telegram.editMessageText(ctx.chat.id, membersMsg.message_id, null, `✅ Экспортировано ${members.length} участников`);

    } catch (error) {
        await ctx.telegram.editMessageText(ctx.chat.id, membersMsg.message_id, null, '❌ Ошибка');
    }
});

bot.command('geo', async (ctx) => {
    const keyboard = Markup.keyboard([
        [Markup.button.locationRequest('📍 Отправить геопозицию')]
    ]).resize();

    await ctx.reply('📍 Отправь геопозицию', { ...keyboard, parse_mode: 'Markdown' });
});

bot.on('location', async (ctx) => {
    const { latitude, longitude } = ctx.message.location;
    
    await ctx.reply(`📍 Ищу людей рядом...`, { parse_mode: 'Markdown' });

    try {
        const users = await getNearbyUsers(latitude, longitude);
        
        if (!users || users.length === 0) {
            await ctx.reply('❌ Никого не найдено рядом');
            return;
        }

        let message = `📍 *Найдено ${users.length} человек:*\n\n`;
        users.slice(0, 20).forEach(u => {
            message += `• ${u.firstName} ${u.lastName || ''}`;
            if (u.username) message += ` (@${u.username})`;
            message += `\n`;
        });

        await ctx.reply(message, { parse_mode: 'Markdown' });

    } catch (error) {
        await ctx.reply('❌ Ошибка');
    }
});

// --- HEALTH CHECK ДЛЯ RENDER (ИСПРАВЛЕННЫЙ) ---
const app = express();
const PORT = process.env.PORT || 10000;

app.get('/', (req, res) => {
    res.send('🤖 OSINT Bot is running!');
});

app.get('/health', (req, res) => {
    res.status(200).send('OK');
});

// ГЛАВНОЕ: привязываемся к 0.0.0.0
app.listen(PORT, '0.0.0.0', () => {
    console.log(`✅ Health check server running on port ${PORT}`);
});

// --- ЗАПУСК БОТА ---
(async () => {
    try {
        await bot.launch({
            dropPendingUpdates: true
        });
        console.log('🤖 Бот запущен через Polling');
        console.log(`📊 Bot: @${bot.botInfo?.username || 'unknown'}`);
    } catch (error) {
        console.error('❌ Ошибка запуска бота:', error.message);
    }
})();

// --- ЗАВЕРШЕНИЕ ---
process.once('SIGINT', () => {
    bot.stop('SIGINT');
    client.disconnect();
    process.exit(0);
});
process.once('SIGTERM', () => {
    bot.stop('SIGTERM');
    client.disconnect();
    process.exit(0);
});
