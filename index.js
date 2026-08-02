const { Telegraf, Markup } = require('telegraf');
const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions');
const axios = require('axios');

// --- ПРОВЕРКА ПЕРЕМЕННЫХ ---
if (!process.env.BOT_TOKEN) {
    console.error('❌ BOT_TOKEN не найден');
    process.exit(1);
}
if (!process.env.API_ID || !process.env.API_HASH) {
    console.error('❌ API_ID или API_HASH не найдены');
    process.exit(1);
}
if (!process.env.SESSION_STRING) {
    console.error('❌ SESSION_STRING не найдена');
    process.exit(1);
}

// --- ИНИЦИАЛИЗАЦИЯ БОТА ---
const bot = new Telegraf(process.env.BOT_TOKEN);

// --- ПОДКЛЮЧЕНИЕ К АККАУНТУ (MTProto) ---
const client = new TelegramClient(
    new StringSession(process.env.SESSION_STRING),
    parseInt(process.env.API_ID),
    process.env.API_HASH,
    {
        connectionRetries: 5,
        useWSS: true,
    }
);

// --- ЗАПУСК КЛИЕНТА ---
(async () => {
    try {
        await client.start();
        const me = await client.getMe();
        console.log(`✅ Авторизован как: ${me.firstName} (@${me.username || 'нет username'})`);
        console.log(`🆔 ID: ${me.id}`);
    } catch (error) {
        console.error('❌ Ошибка авторизации:', error.message);
        process.exit(1);
    }
})();

// --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

// 1. Поиск в соцсетях (Sherlock)
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

// 2. Получение полной инфы о пользователе
async function getFullUserInfo(username) {
    try {
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

// 3. Получение информации о чате
async function getChatInfo(chatId) {
    try {
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

// 4. Экспорт участников чата
async function getChatMembers(chatId, limit = 100) {
    try {
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

// 5. Поиск людей рядом по гео
async function getNearbyUsers(lat, lon, limit = 20) {
    try {
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

// Старт
bot.start(async (ctx) => {
    const welcomeMessage = 
        `🔍 *OSINT Telegram Bot*\n\n` +
        `Я использую ТВОЮ сессию для доступа к данным Telegram.\n\n` +
        `📋 *Команды:*\n` +
        `🔎 /search <username> - Поиск в 170+ соцсетях\n` +
        `👤 /whois <username> - Полная инфо о пользователе\n` +
        `📜 /history <username> - История смены username\n` +
        `📊 /chat <chat_id> - Информация о чате\n` +
        `👥 /members <chat_id> - Экспорт участников\n` +
        `📍 /geo - Найти людей рядом (отправь гео)\n` +
        `❓ /help - Подробная справка\n\n` +
        `💡 *Примеры:*\n` +
        `/whois durov\n` +
        `/chat -100123456789\n` +
        `/members -100123456789 50`;

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

// Обработка кнопок помощи
bot.action('help_whois', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `👤 *Команда /whois*\n\n` +
        `Показывает полную информацию о пользователе:\n` +
        `• ID\n` +
        `• Имя и фамилия\n` +
        `• Username\n` +
        `• Номер телефона (если доступен)\n` +
        `• Бот или нет\n` +
        `• Верификация\n` +
        `• Описание (about)\n` +
        `• Общие чаты\n\n` +
        `*Пример:* /whois durov`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_history', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `📜 *Команда /history*\n\n` +
        `Показывает все прошлые username пользователя.\n\n` +
        `*Пример:* /history durov`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_chat', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `📊 *Команда /chat*\n\n` +
        `Показывает информацию о чате:\n` +
        `• ID\n` +
        `• Название\n` +
        `• Описание\n` +
        `• Количество участников\n` +
        `• Онлайн\n` +
        `• Админы\n` +
        `• И многое другое\n\n` +
        `*Пример:* /chat -100123456789`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_members', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `👥 *Команда /members*\n\n` +
        `Экспортирует список участников чата.\n\n` +
        `*Пример:* /members -100123456789 50\n\n` +
        `Где 50 - количество участников (макс 5000)`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_geo', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `📍 *Команда /geo*\n\n` +
        `Находит людей рядом с тобой.\n\n` +
        `*Как использовать:*\n` +
        `1. Отправь /geo\n` +
        `2. Нажми кнопку "📍 Отправить геопозицию"\n` +
        `3. Бот покажет людей рядом`,
        { parse_mode: 'Markdown' }
    );
});

bot.action('help_search', async (ctx) => {
    await ctx.answerCbQuery();
    await ctx.reply(
        `🔎 *Команда /search*\n\n` +
        `Ищет username на 170+ популярных платформах.\n\n` +
        `*Пример:* /search elonmusk\n\n` +
        `⏱ *Время ожидания:* до 30 секунд`,
        { parse_mode: 'Markdown' }
    );
});

// Помощь
bot.help(async (ctx) => {
    await ctx.reply(
        `📖 *Полная справка*\n\n` +
        `🔎 /search <username> - Поиск в соцсетях (Sherlock)\n` +
        `👤 /whois <username> - Полная инфо о пользователе\n` +
        `📜 /history <username> - История username\n` +
        `📊 /chat <chat_id> - Информация о чате\n` +
        `👥 /members <chat_id> [limit] - Экспорт участников\n` +
        `📍 /geo - Найти людей рядом (отправь гео)\n` +
        `❓ /help - Эта справка\n\n` +
        `*Как получить ID чата:*\n` +
        `1. Добавь бота @userinfobot\n` +
        `2. Перешли любое сообщение из чата\n` +
        `3. Получи ID\n\n` +
        `*Ограничения:*\n` +
        `• Для /whois и /history пользователь должен быть публичным\n` +
        `• Для /chat и /members бот должен быть админом\n` +
        `• Для /geo нужна геопозиция`,
        { parse_mode: 'Markdown' }
    );
});// Поиск в соцсетях
bot.command('search', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи username*\n\n` +
            `*Пример:* /search elonmusk`,
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
        const results = await searchSherlock(username);
        
        if (!results || !results.sites) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                searchMsg.message_id,
                null,
                `❌ *Сервис временно недоступен*\n\nПопробуй позже.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        const foundSites = Object.entries(results.sites)
            .filter(([_, data]) => data.found)
            .slice(0, 20);

        if (foundSites.length === 0) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                searchMsg.message_id,
                null,
                `❌ *Не найдено профилей для ${username}*\n\n` +
                `Проверь правильность написания.`,
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

    } catch (error) {
        console.error('Search error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            searchMsg.message_id,
            null,
            `❌ *Ошибка при поиске*\n\nПопробуй позже.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// WhoIs - полная информация о пользователе
bot.command('whois', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи username*\n\n` +
            `*Пример:* /whois durov`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const username = args[1].replace('@', '');
    const whoMsg = await ctx.reply(
        `👤 *Ищу пользователя @${username}...*`,
        { parse_mode: 'Markdown' }
    );

    try {
        const userInfo = await getFullUserInfo(username);
        
        if (!userInfo) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                whoMsg.message_id,
                null,
                `❌ *Пользователь @${username} не найден*\n\n` +
                `Возможно, профиль скрыт или не существует.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `👤 *Информация о пользователе*\n\n`;
        message += `🆔 ID: \`${userInfo.id}\`\n`;
        message += `📛 Имя: ${userInfo.firstName} ${userInfo.lastName || ''}\n`;
        message += `👤 Username: ${userInfo.username ? '@' + userInfo.username : 'Нет'}\n`;
        message += `📱 Телефон: ${userInfo.phone || 'Скрыт'}\n`;
        message += `🤖 Бот: ${userInfo.isBot ? '✅ Да' : '❌ Нет'}\n`;
        message += `✅ Верифицирован: ${userInfo.verified ? 'Да' : 'Нет'}\n`;
        message += `🔒 Ограничен: ${userInfo.restricted ? 'Да' : 'Нет'}\n`;
        message += `📊 Статус: ${userInfo.status ? '🟢 Онлайн' : '⚫ Оффлайн'}\n`;
        message += `📝 Описание: ${userInfo.about || 'Нет'}\n`;
        message += `👥 Общих чатов: ${userInfo.commonChatsCount || 0}\n`;
        
        if (userInfo.usernames && userInfo.usernames.length > 0) {
            message += `\n📜 *История username:*\n`;
            userInfo.usernames.forEach(u => {
                const active = u.active ? '✅' : '❌';
                message += `• @${u.username} ${active}\n`;
            });
        }

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            whoMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('WhoIs error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            whoMsg.message_id,
            null,
            `❌ *Ошибка при получении информации*\n\n` +
            `Убедись, что пользователь существует и не скрыт.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// История username
bot.command('history', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи username*\n\n` +
            `*Пример:* /history durov`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const username = args[1].replace('@', '');
    const histMsg = await ctx.reply(
        `📜 *Получаю историю @${username}...*`,
        { parse_mode: 'Markdown' }
    );

    try {
        const userInfo = await getFullUserInfo(username);
        
        if (!userInfo) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                histMsg.message_id,
                null,
                `❌ *Пользователь @${username} не найден*`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        if (!userInfo.usernames || userInfo.usernames.length === 0) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                histMsg.message_id,
                null,
                `📜 *Нет истории username для @${username}*\n\n` +
                `Возможно, пользователь никогда не менял username.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `📜 *История username для @${username}:*\n\n`;
        userInfo.usernames.forEach(u => {
            const status = u.active ? '✅ активен' : '❌ неактивен';
            message += `• @${u.username} - ${status}\n`;
        });

        message += `\n_Всего: ${userInfo.usernames.length} username_`;

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            histMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('History error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            histMsg.message_id,
            null,
            `❌ *Ошибка при получении истории*`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Информация о чате
bot.command('chat', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи ID чата*\n\n` +
            `*Пример:* /chat -100123456789`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const chatId = parseInt(args[1]);
    if (isNaN(chatId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const chatMsg = await ctx.reply(
        `📊 *Получаю информацию о чате...*`,
        { parse_mode: 'Markdown' }
    );

    try {
        const info = await getChatInfo(chatId);
        
        if (!info) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                chatMsg.message_id,
                null,
                `❌ *Не удалось получить информацию*\n\n` +
                `Бот должен быть администратором чата.`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `📊 *Информация о чате*\n\n`;
        message += `🆔 ID: \`${info.id}\`\n`;
        message += `📛 Название: *${info.title}*\n`;
        message += `👤 Username: ${info.username ? '@' + info.username : 'Нет'}\n`;
        message += `📝 Описание: ${info.about || 'Нет'}\n`;
        message += `👥 Участников: *${info.participantsCount}*\n`;
        message += `🟢 Онлайн: *${info.onlineCount || 0}*\n`;
        message += `👑 Админов: *${info.adminsCount || 0}*\n`;
        message += `🚫 Забанено: ${info.bannedCount || 0}\n`;
        message += `🔒 Публичный: ${info.isPublic ? '✅ Да' : '❌ Нет'}\n`;
        message += `🔗 Ссылка: ${info.inviteLink || 'Нет'}\n`;
        message += `⏱ Медленный режим: ${info.slowMode || 0} сек\n`;
        message += `📜 Скрытая история: ${info.hiddenPrehistory ? '✅ Да' : '❌ Нет'}`;

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            chatMsg.message_id,
            null,
            message,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('Chat error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            chatMsg.message_id,
            null,
            `❌ *Ошибка при получении информации*\n\n` +
            `Убедись, что бот является администратором чата.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Экспорт участников
bot.command('members', async (ctx) => {
    const args = ctx.message.text.split(' ');
    if (args.length < 2) {
        await ctx.reply(
            `❌ *Укажи ID чата*\n\n` +
            `*Пример:* /members -100123456789 50\n\n` +
            `Где 50 - количество участников (макс 5000)`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    const chatId = parseInt(args[1]);
    const limit = args[2] ? Math.min(parseInt(args[2]), 5000) : 100;

    if (isNaN(chatId)) {
        await ctx.reply('❌ ID должен быть числом');
        return;
    }

    const membersMsg = await ctx.reply(
        `👥 *Экспортирую ${limit} участников...*\n` +
        `⏱ Это может занять некоторое время`,
        { parse_mode: 'Markdown' }
    );

    try {
        const members = await getChatMembers(chatId, limit);
        
        if (!members || members.length === 0) {
            await ctx.telegram.editMessageText(
                ctx.chat.id,
                membersMsg.message_id,
                null,
                `❌ *Не удалось получить участников*\n\n` +
                `Бот должен быть администратором чата.`,
                { parse_mode: 'Markdown' }
            );
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

        await ctx.telegram.editMessageText(
            ctx.chat.id,
            membersMsg.message_id,
            null,
            `✅ *Экспортировано ${members.length} участников*\n\n` +
            `Файл отправлен в формате CSV.`,
            { parse_mode: 'Markdown' }
        );

    } catch (error) {
        console.error('Members error:', error);
        await ctx.telegram.editMessageText(
            ctx.chat.id,
            membersMsg.message_id,
            null,
            `❌ *Ошибка при экспорте*\n\n` +
            `Убедись, что бот является администратором чата.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Geo - поиск людей рядом
bot.command('geo', async (ctx) => {
    const keyboard = Markup.keyboard([
        [Markup.button.locationRequest('📍 Отправить геопозицию')]
    ]).resize();

    await ctx.reply(
        `📍 *Найти людей рядом*\n\n` +
        `Нажми кнопку ниже и отправь свою геопозицию.\n\n` +
        `*Важно:* работают только пользователи, которые есть у тебя в контактах.`,
        {
            parse_mode: 'Markdown',
            ...keyboard
        }
    );
});

// Обработка геопозиции
bot.on('location', async (ctx) => {
    const { latitude, longitude } = ctx.message.location;
    
    await ctx.reply(
        `📍 *Ищу людей рядом с тобой...*\n` +
        `🌍 Координаты: ${latitude}, ${longitude}`,
        { parse_mode: 'Markdown' }
    );

    try {
        const users = await getNearbyUsers(latitude, longitude);
        
        if (!users || users.length === 0) {
            await ctx.reply(
                `❌ *Никого не найдено рядом*\n\n` +
                `Попробуй:\n` +
                `• Подойти ближе к людям\n` +
                `• Добавить их в контакты\n` +
                `• Проверить, включена ли у них геопозиция`,
                { parse_mode: 'Markdown' }
            );
            return;
        }

        let message = `📍 *Найдено ${users.length} человек рядом:*\n\n`;
        users.slice(0, 20).forEach(u => {
            message += `• ${u.firstName} ${u.lastName || ''}`;
            if (u.username) message += ` (@${u.username})`;
            if (u.phone && u.phone !== 'Скрыт') message += ` 📱${u.phone}`;
            message += `\n`;
        });

        await ctx.reply(message, { parse_mode: 'Markdown' });

    } catch (error) {
        console.error('Geo error:', error);
        await ctx.reply(
            `❌ *Ошибка при поиске*\n\n` +
            `Возможно, у тебя нет контактов рядом.`,
            { parse_mode: 'Markdown' }
        );
    }
});

// Обработка текста (не команд)
bot.on('text', async (ctx) => {
    const text = ctx.message.text;

    if (text.includes('t.me/') && !text.includes('/search') && !text.includes('/whois')) {
        const username = text.split('t.me/')[1]?.split('/')[0]?.split('?')[0];
        if (username) {
            await ctx.reply(
                `🔍 *Найти @${username}?*\n\n` +
                `Используй команды:\n` +
                `/whois ${username} - Полная информация\n` +
                `/search ${username} - Поиск в соцсетях\n` +
                `/history ${username} - История username`,
                { parse_mode: 'Markdown' }
            );
            return;
        }
    }

    if (text.startsWith('@') && text.length > 1) {
        const username = text.substring(1).split(' ')[0];
        await ctx.reply(
            `👤 *Найти ${username}?*\n\n` +
            `Используй команды:\n` +
            `/whois ${username} - Полная информация\n` +
            `/search ${username} - Поиск в соцсетях\n` +
            `/history ${username} - История username`,
            { parse_mode: 'Markdown' }
        );
        return;
    }

    if (text.startsWith('-') && text.length > 5) {
        const chatId = parseInt(text);
        if (!isNaN(chatId)) {
            await ctx.reply(
                `📊 *Найти чат ${chatId}?*\n\n` +
                `Используй команду:\n` +
                `/chat ${chatId}\n\n` +
                `Для экспорта участников:\n` +
                `/members ${chatId}`,
                { parse_mode: 'Markdown' }
            );
            return;
        }
    }

    await ctx.reply(
        `❓ *Неизвестная команда*\n\n` +
        `Используй /start для просмотра всех команд.\n\n` +
        `*Быстрый старт:*\n` +
        `/whois durov - Информация о пользователе\n` +
        `/search elonmusk - Поиск в соцсетях`,
        { parse_mode: 'Markdown' }
    );
});

// --- ЗАПУСК БОТА (POLLING) ---
bot.launch({
    dropPendingUpdates: true
}).then(() => {
    console.log('🤖 Бот запущен через Polling');
    console.log(`📊 Bot: @${bot.botInfo?.username || 'unknown'}`);
}).catch((err) => {
    console.error('❌ Ошибка запуска:', err);
    process.exit(1);
});

// Graceful stop
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
