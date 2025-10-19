# discord_bot.py - с добавленными текстовыми командами

import os
import asyncio
import aiohttp
import json
import re
from typing import Optional

try:
    import discord
    from discord.ext import commands
    from discord import app_commands
except ImportError as e:
    print(f"❌ Ошибка импорта discord: {e}")
    print("Убедитесь, что установлена правильная версия: pip install discord.py")
    exit(1)

# Настройки из переменных окружения
DISCORD_TOKEN = ''
BOT_PREFIX = os.getenv('BOT_PREFIX', '!')
API_SECRET = os.getenv('API_SECRET', 'default_secret')
API_BASE_URL = os.getenv('API_BASE_URL', 'http://localhost:5000')

class MinecraftBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix=commands.when_mentioned_or(BOT_PREFIX),
            intents=intents,
            help_command=None
        )
        
        # Состояние для интерактивной настройки
        self.setup_sessions = {}
        self.plugin_sessions = {}

    async def setup_hook(self):
        """Синхронизация команд при запуске"""
        try:
            # Явно добавляем команды в дерево команд
            self.tree.add_command(ServerStatusCommand())
            self.tree.add_command(ServerStartCommand())
            self.tree.add_command(ServerStopCommand())
            self.tree.add_command(ServerRestartCommand())
            self.tree.add_command(ServerListCommand())
            self.tree.add_command(ServerLogsCommand())
            self.tree.add_command(ServerCommandCommand())
            self.tree.add_command(ServerCreateCommand())
            self.tree.add_command(HelpCommand())
            self.tree.add_command(ServerPluginsCommand())
            self.tree.add_command(ServerInstallPluginCommand())
            self.tree.add_command(ServerRemovePluginCommand())
            self.tree.add_command(ServerPluginFromURLCommand())
            
            # Синхронизируем команды
            synced = await self.tree.sync()
            print(f"✅ Slash-команды синхронизированы: {len(synced)} команд")
            
        except Exception as e:
            print(f"❌ Ошибка синхронизации команд: {e}")
        
    async def on_ready(self):
        print(f'✅ Бот авторизован как {self.user} (ID: {self.user.id})')
        print(f'✅ Префикс команд: {BOT_PREFIX}')
        print('------')
        
        # Установка статуса бота
        activity = discord.Activity(type=discord.ActivityType.watching, name="Minecraft серверы")
        await self.change_presence(activity=activity)

    async def api_request(self, endpoint: str, method: str = 'GET', data: dict = None):
        """Универсальный метод для API запросов"""
        url = f"{API_BASE_URL}{endpoint}"
        headers = {"X-API-Key": API_SECRET, "Content-Type": "application/json"}
        
        try:
            async with aiohttp.ClientSession() as session:
                if method.upper() == 'GET':
                    async with session.get(url, headers=headers, timeout=10) as response:
                        return await response.json(), response.status
                elif method.upper() == 'POST':
                    async with session.post(url, headers=headers, json=data, timeout=10) as response:
                        return await response.json(), response.status
        except aiohttp.ClientError as e:
            return {"error": f"Network error: {str(e)}"}, 500
        except asyncio.TimeoutError:
            return {"error": "Request timeout"}, 500
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}, 500

    async def api_upload(self, endpoint: str, file_data: dict):
        """Метод для загрузки файлов через API"""
        url = f"{API_BASE_URL}{endpoint}"
        headers = {"X-API-Key": API_SECRET}
        
        try:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field('file', file_data['bytes'], filename=file_data['filename'])
                if 'path' in file_data:
                    data.add_field('path', file_data['path'])
                
                async with session.post(url, headers=headers, data=data) as response:
                    return await response.json(), response.status
        except aiohttp.ClientError as e:
            return {"error": f"Network error: {str(e)}"}, 500
        except asyncio.TimeoutError:
            return {"error": "Request timeout"}, 500
        except Exception as e:
            return {"error": f"Unexpected error: {str(e)}"}, 500

    async def download_file(self, url: str):
        """Скачивание файла по URL"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        filename = url.split('/')[-1]
                        content = await response.read()
                        return filename, content
                    else:
                        return None, None
        except Exception as e:
            print(f"❌ Ошибка скачивания файла: {e}")
            return None, None

    # ТЕКСТОВЫЕ КОМАНДЫ ДЛЯ ПЛАГИНОВ
    @commands.command(name='server_plugins')
    async def server_plugins_text(self, ctx, port: int):
        """Текстовая команда для показа плагинов"""
        async with ctx.typing():
            data, status = await self.api_request(f"/api/server/status/{port}")
            
            if status != 200:
                await ctx.send(f"❌ Сервер на порту {port} не найден")
                return
                
            server_type = data.get('server_type', 'vanilla')
            
            # Определяем папку с плагинами/модами
            if server_type in ['paper', 'purpur']:
                folder = 'plugins'
            elif server_type == 'fabric':
                folder = 'mods'
            else:
                folder = 'plugins'
                
            # Получаем список файлов
            files_data, files_status = await self.api_request(f"/server_files/{port}?path={folder}")
            
            if files_status != 200:
                await ctx.send(f"❌ Не удалось получить список плагинов: {files_data.get('error', 'Неизвестная ошибка')}")
                return
                
            files = files_data.get('files', [])
            jar_files = [f for f in files if f['name'].lower().endswith('.jar')]
            
            if not jar_files:
                embed = discord.Embed(
                    title=f"📦 Плагины/моды сервера {port}",
                    description=f"В папке `{folder}` нет JAR-файлов",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
                return
                
            embed = discord.Embed(
                title=f"📦 Плагины/моды сервера {port}",
                description=f"Найдено {len(jar_files)} JAR-файлов в папке `{folder}`",
                color=discord.Color.blue()
            )
            
            # Разбиваем на группы по 10 файлов
            for i in range(0, len(jar_files), 10):
                group = jar_files[i:i+10]
                files_list = "\n".join([f"• `{f['name']}` ({f['size'] // 1024} KB)" for f in group])
                embed.add_field(
                    name=f"Файлы {i//10 + 1}",
                    value=files_list if files_list else "Нет файлов",
                    inline=False
                )
                
            await ctx.send(embed=embed)

    @commands.command(name='server_install_plugin')
    async def server_install_plugin_text(self, ctx, port: int):
        """Текстовая команда для интерактивной установки плагина"""
        user_id = ctx.author.id
        
        # Проверяем существование сервера
        server_data, status = await self.api_request(f"/api/server/status/{port}")
        if status != 200:
            await ctx.send(f"❌ Сервер на порту {port} не найден")
            return
            
        # Проверяем, не активна ли уже сессия
        if user_id in self.plugin_sessions:
            await ctx.send("❌ У вас уже есть активная сессия установки. Завершите её сначала.")
            return
            
        # Начинаем новую сессию
        self.plugin_sessions[user_id] = {
            'step': 1,
            'port': port,
            'server_type': server_data.get('server_type', 'vanilla'),
            'filename': None,
            'file_content': None,
            'folder': None
        }
        
        embed = discord.Embed(
            title="🔌 Установка плагина/мода",
            description=(
                f"**Сервер:** порт {port}\n"
                f"**Тип:** {server_data.get('server_type', 'vanilla')}\n\n"
                "**Шаг 1/2:** Прикрепите JAR-файл плагина/мода к сообщению и отправьте URL скачивания:\n"
                "(или прямой URL к JAR-файлу)"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Прикрепите файл или введите URL. Для отмены введите 'отмена'")
        
        await ctx.send(embed=embed)

    @commands.command(name='server_remove_plugin')
    async def server_remove_plugin_text(self, ctx, port: int, filename: str):
        """Текстовая команда для удаления плагина"""
        async with ctx.typing():
            # Получаем информацию о сервере
            server_data, status = await self.api_request(f"/api/server/status/{port}")
            if status != 200:
                await ctx.send(f"❌ Сервер на порту {port} не найден")
                return
                
            server_type = server_data.get('server_type', 'vanilla')
            
            # Определяем папку с плагинами/модами
            if server_type in ['paper', 'purpur']:
                folder = 'plugins'
            elif server_type == 'fabric':
                folder = 'mods'
            else:
                folder = 'plugins'
                
            # Формируем путь к файлу
            file_path = f"{folder}/{filename}"
            if not file_path.endswith('.jar'):
                file_path += '.jar'
                
            # Удаляем файл
            delete_data = {'path': file_path}
            result, delete_status = await self.api_request(f"/server_files/{port}/delete", "POST", delete_data)
            
            if delete_status == 200:
                embed = discord.Embed(
                    title="✅ Плагин/мод удален",
                    description=f"Файл `{filename}` удален с сервера {port}",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"❌ Ошибка удаления: {result.get('error', 'Неизвестная ошибка')}")

    @commands.command(name='server_plugin_url')
    async def server_plugin_url_text(self, ctx, port: int, url: str):
        """Текстовая команда для установки плагина по прямой ссылке"""
        async with ctx.typing():
            # Проверяем существование сервера
            server_data, status = await self.api_request(f"/api/server/status/{port}")
            if status != 200:
                await ctx.send(f"❌ Сервер на порту {port} не найден")
                return
                
            # Скачиваем файл
            filename, file_content = await self.download_file(url)
            
            if not filename or not file_content:
                await ctx.send("❌ Не удалось скачать файл. Проверьте URL и попробуйте снова.")
                return
                
            # Определяем папку назначения
            server_type = server_data.get('server_type', 'vanilla')
            if server_type in ['paper', 'purpur']:
                folder = 'plugins'
            elif server_type == 'fabric':
                folder = 'mods'
            else:
                folder = 'plugins'
                
            # Загружаем файл на сервер
            file_data = {
                'bytes': file_content,
                'filename': filename,
                'path': folder
            }
            
            result, upload_status = await self.api_upload(f"/server_files/{port}/upload", file_data)
            
            if upload_status == 200:
                embed = discord.Embed(
                    title="✅ Плагин/мод установлен!",
                    description=(
                        f"**Файл:** {filename}\n"
                        f"**Сервер:** порт {port}\n"
                        f"**Папка:** {folder}\n\n"
                        "Для применения изменений может потребоваться перезагрузка сервера."
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"❌ Ошибка загрузки: {result.get('error', 'Неизвестная ошибка')}")

    @commands.command(name='plugins_help')
    async def plugins_help_text(self, ctx):
        """Текстовая команда справки по плагинам"""
        embed = discord.Embed(
            title="🔌 Помощь по управлению плагинами/модами",
            description="Доступные текстовые команды:",
            color=discord.Color.blue()
        )
        
        commands_list = [
            (f"{BOT_PREFIX}server_plugins <port>", "Показать список плагинов/модов сервера"),
            (f"{BOT_PREFIX}server_install_plugin <port>", "Интерактивная установка плагина/мода"),
            (f"{BOT_PREFIX}server_plugin_url <port> <url>", "Установить плагин/мод по прямой ссылке"),
            (f"{BOT_PREFIX}server_remove_plugin <port> <filename>", "Удалить плагин/мод с сервера"),
            (f"{BOT_PREFIX}plugins_help", "Показать эту справку")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
            
        await ctx.send(embed=embed)

    async def handle_setup_message(self, message: discord.Message):
        """Обработка сообщений для интерактивной настройки"""
        user_id = message.author.id
        
        if user_id not in self.setup_sessions:
            return False
            
        session = self.setup_sessions[user_id]
        content = message.content.strip()
        
        # Проверка на отмену
        if content.lower() in ['отмена', 'cancel', 'stop']:
            del self.setup_sessions[user_id]
            await message.channel.send("❌ Настройка сервера отменена.")
            return True
            
        try:
            if session['step'] == 1:
                # Шаг 1: Порт
                try:
                    port = int(content)
                    if port < 25565 or port > 26000:
                        await message.channel.send("❌ Порт должен быть в диапазоне 25565-26000")
                        return True
                        
                    # Проверяем, свободен ли порт
                    data, status = await self.api_request(f"/api/server/status/{port}")
                    if status == 200:
                        await message.channel.send("❌ Этот порт уже занят другим сервером")
                        return True
                        
                    session['port'] = port
                    session['step'] = 2
                    
                    embed = discord.Embed(
                        title="🎮 Создание нового Minecraft сервера",
                        description=(
                            f"✅ Порт: {port}\n\n"
                            "**Шаг 2/4:** Выберите тип сервера:\n"
                            "• `vanilla` - Официальный сервер Mojang\n"
                            "• `paper` - Высокопроизводительный форк Spigot\n"
                            "• `purpur` - Оптимизированный форк Paper\n"
                            "• `fabric` - Легковесная модовая платформа\n"
                            "• `mrpack` - Modrinth модпак"
                        ),
                        color=discord.Color.blue()
                    )
                    await message.channel.send(embed=embed)
                    return True
                    
                except ValueError:
                    await message.channel.send("❌ Неверный формат порта. Укажите число от 25565 до 26000")
                    return True
                    
            elif session['step'] == 2:
                # Шаг 2: Тип сервера
                server_type = content.lower()
                valid_types = ['vanilla', 'paper', 'purpur', 'fabric', 'mrpack']
                
                if server_type not in valid_types:
                    await message.channel.send(
                        "❌ Неизвестный тип сервера. Доступные типы: `vanilla`, `paper`, `purpur`, `fabric`, `mrpack`"
                    )
                    return True
                    
                session['server_type'] = server_type
                session['step'] = 3
                
                if server_type == 'mrpack':
                    # Для mrpack показываем специальные инструкции
                    embed = discord.Embed(
                        title="🎮 Создание нового Minecraft сервера",
                        description=(
                            f"✅ Порт: {session['port']}\n"
                            f"✅ Тип: {server_type}\n\n"
                            "**Шаг 3/4:** Для модпаков укажите:\n"
                            "• `uploaded:[your-file-uuid].mrpack` - для загруженного файла (замените [your-file-uuid] на реальный ID файла, полученный после загрузки через веб-интерфейс или API)\n"
                            "• `modpack_id:version` - для установки по ID\n"
                            "• `modpack_id` - для последней версии\n\n"
                            "Примеры:\n"
                            "• `uploaded:abcdef12-3456-7890-abcd-ef1234567890.mrpack` (используйте ваш реальный UUID)\n"
                            "• `fabulous-fabric-5:2.0.1`\n"
                            "• `bewitchment`\n\n"
                            "**Важно:** Для uploaded используйте точное значение, возвращенное после загрузки файла! Если файл не загружен, сначала загрузите его через /upload_mrpack."
                        ),
                        color=discord.Color.blue()
                    )
                else:
                    embed = discord.Embed(
                        title="🎮 Создание нового Minecraft сервера",
                        description=(
                            f"✅ Порт: {session['port']}\n"
                            f"✅ Тип: {server_type}\n\n"
                            "**Шаг 3/4:** Укажите версию Minecraft (например: `1.20.1`):"
                        ),
                        color=discord.Color.blue()
                    )
                await message.channel.send(embed=embed)
                return True
                
            elif session['step'] == 3:
                # Шаг 3: Версия
                version = content.strip()
                
                if not version:
                    await message.channel.send("❌ Укажите версию сервера")
                    return True
                    
                session['version'] = version
                session['step'] = 4
                
                embed = discord.Embed(
                    title="🎮 Создание нового Minecraft сервера",
                    description=(
                        f"✅ Порт: {session['port']}\n"
                        f"✅ Тип: {session['server_type']}\n"
                        f"✅ Версия: {version}\n\n"
                        "**Шаг 4/4:** Укажите объем памяти (например: `4G` или `2048M`):"
                    ),
                    color=discord.Color.blue()
                )
                await message.channel.send(embed=embed)
                return True
                
            elif session['step'] == 4:
                # Шаг 4: Память
                memory = content.strip()
                if not re.match(r'^\d+[MG]$', memory):
                    await message.channel.send("❌ Неверный формат памяти. Примеры: `4G`, `2048M`")
                    return True
                    
                session['memory'] = memory
                
                # Создаем сервер
                create_data = {
                    'port': session['port'],
                    'version': session['version'],
                    'server_type': session['server_type'],
                    'memory': memory,
                    'auto_start': True
                }
                
                result, status = await self.api_request("/create_server", "POST", create_data)
                
                if status == 200:
                    embed = discord.Embed(
                        title="✅ Сервер создан!",
                        description=(
                            f"**Порт:** {session['port']}\n"
                            f"**Тип:** {session['server_type']}\n"
                            f"**Версия:** {session['version']}\n"
                            f"**Память:** {memory}\n\n"
                            "Сервер запущен автоматически!"
                        ),
                        color=discord.Color.green()
                    )
                else:
                    embed = discord.Embed(
                        title="❌ Ошибка создания сервера",
                        description=f"Ошибка: {result.get('error', 'Неизвестная ошибка')}",
                        color=discord.Color.red()
                    )
                
                await message.channel.send(embed=embed)
                del self.setup_sessions[user_id]
                return True
                
        except Exception as e:
            await message.channel.send(f"❌ Ошибка: {str(e)}")
            del self.setup_sessions[user_id]
            return True

    async def on_message(self, message: discord.Message):
        """Обработка текстовых сообщений"""
        if message.author.bot:
            return
            
        # Обработка сообщений для настройки сервера
        if await self.handle_setup_message(message):
            return
            
        # Обработка сообщений для установки плагинов
        user_id = message.author.id
        if user_id in self.plugin_sessions:
            session = self.plugin_sessions[user_id]
            
            if message.content.lower() in ['отмена', 'cancel', 'stop']:
                del self.plugin_sessions[user_id]
                await message.channel.send("❌ Установка плагина/мода отменена.")
                return
                
            if session['step'] == 1:
                url = message.content.strip()
                attachments = message.attachments
                
                if attachments:
                    # Если прикреплен файл
                    attachment = attachments[0]
                    if not attachment.filename.lower().endswith('.jar'):
                        await message.channel.send("❌ Файл должен быть в формате JAR")
                        del self.plugin_sessions[user_id]
                        return
                        
                    session['filename'] = attachment.filename
                    session['file_content'] = await attachment.read()
                    session['step'] = 2
                    
                    # Определяем папку назначения
                    if session['server_type'] in ['paper', 'purpur']:
                        session['folder'] = 'plugins'
                    elif session['server_type'] == 'fabric':
                        session['folder'] = 'mods'
                    else:
                        session['folder'] = 'plugins'
                    
                    embed = discord.Embed(
                        title="🔌 Установка плагина/мода",
                        description=(
                            f"**Сервер:** порт {session['port']}\n"
                            f"**Файл:** {session['filename']}\n"
                            f"**Папка назначения:** {session['folder']}\n\n"
                            "**Шаг 2/2:** Подтвердите установку или введите 'отмена'"
                        ),
                        color=discord.Color.blue()
                    )
                    await message.channel.send(embed=embed)
                    return
                    
                elif url:
                    # Если указан URL
                    filename, file_content = await self.download_file(url)
                    
                    if not filename or not file_content:
                        await message.channel.send("❌ Не удалось скачать файл. Проверьте URL и попробуйте снова.")
                        del self.plugin_sessions[user_id]
                        return
                        
                    if not filename.lower().endswith('.jar'):
                        await message.channel.send("❌ Файл должен быть в формате JAR")
                        del self.plugin_sessions[user_id]
                        return
                        
                    session['filename'] = filename
                    session['file_content'] = file_content
                    session['step'] = 2
                    
                    # Определяем папку назначения
                    if session['server_type'] in ['paper', 'purpur']:
                        session['folder'] = 'plugins'
                    elif session['server_type'] == 'fabric':
                        session['folder'] = 'mods'
                    else:
                        session['folder'] = 'plugins'
                    
                    embed = discord.Embed(
                        title="🔌 Установка плагина/мода",
                        description=(
                            f"**Сервер:** порт {session['port']}\n"
                            f"**Файл:** {session['filename']}\n"
                            f"**Папка назначения:** {session['folder']}\n\n"
                            "**Шаг 2/2:** Подтвердите установку или введите 'отмена'"
                        ),
                        color=discord.Color.blue()
                    )
                    await message.channel.send(embed=embed)
                    return
                    
                else:
                    await message.channel.send("❌ Пожалуйста, прикрепите JAR-файл или укажите URL")
                    return
                    
            elif session['step'] == 2:
                # Подтверждение установки
                if message.content.lower() in ['подтвердить', 'ок', 'yes', 'ok']:
                    file_data = {
                        'bytes': session['file_content'],
                        'filename': session['filename'],
                        'path': session['folder']
                    }
                    
                    result, status = await self.api_upload(f"/server_files/{session['port']}/upload", file_data)
                    
                    if status == 200:
                        embed = discord.Embed(
                            title="✅ Плагин/мод установлен!",
                            description=(
                                f"**Файл:** {session['filename']}\n"
                                f"**Сервер:** порт {session['port']}\n"
                                f"**Папка:** {session['folder']}\n\n"
                                "Для применения изменений может потребоваться перезагрузка сервера."
                            ),
                            color=discord.Color.green()
                        )
                    else:
                        embed = discord.Embed(
                            title="❌ Ошибка установки",
                            description=f"Ошибка: {result.get('error', 'Неизвестная ошибка')}",
                            color=discord.Color.red()
                        )
                    
                    await message.channel.send(embed=embed)
                    del self.plugin_sessions[user_id]
                    return
                    
                else:
                    await message.channel.send("❌ Установка отменена")
                    del self.plugin_sessions[user_id]
                    return
                
        await self.process_commands(message)

class ServerStatusCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_status",
            description="Показать статус сервера",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request(f"/api/server/status/{port}")
        
        if status == 200:
            status_text = "Запущен" if data['status'] == 'running' else "Остановлен"
            status_emoji = "🟢" if data['status'] == 'running' else "🔴"
            
            embed = discord.Embed(
                title=f"{status_emoji} Статус сервера {port}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Тип", value=data.get('server_type', 'vanilla'), inline=True)
            embed.add_field(name="Версия", value=data.get('version', 'N/A'), inline=True)
            embed.add_field(name="Статус", value=status_text, inline=True)
            embed.add_field(name="Память", value=data.get('memory', '2G'), inline=True)
            embed.add_field(name="MOTD", value=data.get('motd', 'Minecraft Server'), inline=False)
            embed.add_field(name="Макс. игроков", value=str(data.get('max_players', 20)), inline=True)
            
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Сервер не найден')}")

class ServerStartCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_start",
            description="Запустить сервер",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request(f"/start_server/{port}", "POST")
        
        if status == 200:
            embed = discord.Embed(
                title="🚀 Сервер запускается",
                description=f"Сервер на порту {port} запускается...",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Не удалось запустить сервер')}")

class ServerStopCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_stop",
            description="Остановить сервер",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request(f"/stop_server/{port}", "POST")
        
        if status == 200:
            embed = discord.Embed(
                title="🛑 Сервер останавливается",
                description=f"Сервер на порту {port} останавливается...",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Не удалось остановить сервер')}")

class ServerRestartCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_restart",
            description="Перезапустить сервер",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        # Сначала останавливаем
        stop_data, stop_status = await bot.api_request(f"/stop_server/{port}", "POST")
        
        if stop_status != 200:
            await interaction.followup.send(f"❌ Ошибка при остановке: {stop_data.get('error', 'Неизвестная ошибка')}")
            return
            
        # Ждем 5 секунд
        await asyncio.sleep(5)
        
        # Затем запускаем
        start_data, start_status = await bot.api_request(f"/start_server/{port}", "POST")
        
        if start_status == 200:
            embed = discord.Embed(
                title="🔄 Сервер перезапущен",
                description=f"Сервер на порту {port} успешно перезапущен",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка при запуске: {start_data.get('error', 'Неизвестная ошибка')}")

class ServerListCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_list",
            description="Показать список всех серверов",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request("/list_servers")
        
        if status == 200:
            servers = data.get('servers', [])
            
            if not servers:
                embed = discord.Embed(
                    title="📭 Нет активных серверов",
                    description="Создайте первый сервер с помощью команды `/server_create`",
                    color=discord.Color.blue()
                )
                await interaction.followup.send(embed=embed)
                return
                
            embed = discord.Embed(
                title="📊 Активные серверы",
                color=discord.Color.blue()
            )
            
            for server in servers:
                status_emoji = "🟢" if server['status'] == 'running' else "🔴"
                status_text = "Запущен" if server['status'] == 'running' else "Остановлен"
                
                embed.add_field(
                    name=f"{status_emoji} Порт {server['port']}",
                    value=(
                        f"**Тип:** {server['server_type']}\n"
                        f"**Версия:** {server['version']}\n"
                        f"**Статус:** {status_text}\n"
                        f"**Память:** {server.get('memory', '2G')}"
                    ),
                    inline=True
                )
                
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Не удалось получить список серверов')}")

class ServerLogsCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_logs",
            description="Показать последние логи сервера",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int, lines: int = 10):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request(f"/server_status/{port}")
        
        if status == 200:
            logs = data.get('logs', [])
            
            if not logs:
                await interaction.followup.send(f"📭 Нет логов для сервера на порту {port}")
                return
                
            # Ограничиваем количество логов
            logs = logs[-lines:] if lines > 0 else logs
            logs_text = "\n".join(logs)
            
            # Если логи слишком длинные, разбиваем на части
            if len(logs_text) > 2000:
                logs_text = logs_text[-2000:]
                logs_text = "..." + logs_text
                
            embed = discord.Embed(
                title=f"📋 Логи сервера {port}",
                description=f"```\n{logs_text}\n```",
                color=discord.Color.dark_gray()
            )
            embed.set_footer(text=f"Показано последних {len(logs)} строк")
            
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Сервер не найден')}")

class ServerCommandCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_command",
            description="Выполнить команду на сервере",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int, command: str):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        data, status = await bot.api_request(
            f"/api/server/command/{port}", 
            "POST", 
            {"command": command}
        )
        
        if status == 200:
            embed = discord.Embed(
                title="✅ Команда отправлена",
                description=f"Команда `{command}` выполнена на сервере {port}",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка: {data.get('error', 'Не удалось выполнить команду')}")

class ServerCreateCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_create",
            description="Интерактивное создание нового сервера",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        bot = interaction.client
        
        # Проверяем, не активна ли уже сессия
        if user_id in bot.setup_sessions:
            await interaction.response.send_message(
                "❌ У вас уже есть активная сессия настройки. Завершите её сначала.",
                ephemeral=True
            )
            return
            
        # Начинаем новую сессию
        bot.setup_sessions[user_id] = {
            'step': 1,
            'port': None,
            'server_type': None,
            'version': None,
            'memory': '2G'
        }
        
        embed = discord.Embed(
            title="🎮 Создание нового Minecraft сервера",
            description=(
                "Добро пожаловать в мастер настройки!\n\n"
                "**Шаг 1/4:** Укажите порт для сервера (25565-26000):"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Введите порт в чат или 'отмена' для отмены")
        
        await interaction.response.send_message(embed=embed)

class ServerPluginsCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_plugins",
            description="Показать список плагинов/модов сервера",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        
        # Получаем информацию о сервере
        server_data, status = await bot.api_request(f"/api/server/status/{port}")
        if status != 200:
            await interaction.followup.send(f"❌ Сервер на порту {port} не найден")
            return
            
        server_type = server_data.get('server_type', 'vanilla')
        
        # Определяем папку с плагинами/модами
        if server_type in ['paper', 'purpur']:
            folder = 'plugins'
        elif server_type == 'fabric':
            folder = 'mods'
        else:
            folder = 'plugins'  # По умололчанию для vanilla и других
            
        # Получаем список файлов
        files_data, files_status = await bot.api_request(f"/server_files/{port}?path={folder}")
        
        if files_status != 200:
            await interaction.followup.send(f"❌ Не удалось получить список плагинов: {files_data.get('error', 'Неизвестная ошибка')}")
            return
            
        files = files_data.get('files', [])
        jar_files = [f for f in files if f['name'].lower().endswith('.jar')]
        
        if not jar_files:
            embed = discord.Embed(
                title=f"📦 Плагины/моды сервера {port}",
                description=f"В папке `{folder}` нет JAR-файлов",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
            return
            
        embed = discord.Embed(
            title=f"📦 Плагины/моды сервера {port}",
            description=f"Найдено {len(jar_files)} JAR-файлов в папке `{folder}`",
            color=discord.Color.blue()
        )
        
        # Разбиваем на группы по 10 файлов (из-за ограничения Discord на количество полей)
        for i in range(0, len(jar_files), 10):
            group = jar_files[i:i+10]
            files_list = "\n".join([f"• `{f['name']}` ({f['size'] // 1024} KB)" for f in group])
            embed.add_field(
                name=f"Файлы {i//10 + 1}",
                value=files_list if files_list else "Нет файлов",
                inline=False
            )
            
        await interaction.followup.send(embed=embed)

class ServerInstallPluginCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_install_plugin",
            description="Интерактивная установка плагина/мода из файла",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int):
        user_id = interaction.user.id
        bot = interaction.client
        
        # Проверяем существование сервера
        server_data, status = await bot.api_request(f"/api/server/status/{port}")
        if status != 200:
            await interaction.response.send_message(
                f"❌ Сервер на порту {port} не найден",
                ephemeral=True
            )
            return
            
        # Проверяем, не активна ли уже сессия
        if user_id in bot.plugin_sessions:
            await interaction.response.send_message(
                "❌ У вас уже есть активная сессия установки. Завершите её сначала.",
                ephemeral=True
            )
            return
            
        # Начинаем новую сессию
        bot.plugin_sessions[user_id] = {
            'step': 1,
            'port': port,
            'server_type': server_data.get('server_type', 'vanilla'),
            'filename': None,
            'file_content': None,
            'folder': None
        }
        
        embed = discord.Embed(
            title="🔌 Установка плагина/мода",
            description=(
                f"**Сервер:** порт {port}\n"
                f"**Тип:** {server_data.get('server_type', 'vanilla')}\n\n"
                "**Шаг 1/2:** Прикрепите JAR-файл плагина/мода к сообщению и отправьте URL скачивания:\n"
                "(или прямой URL к JAR-файлу)"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Прикрепите файл или введите URL. Для отмены введите 'отмена'")
        
        await interaction.response.send_message(embed=embed)

class ServerRemovePluginCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_remove_plugin",
            description="Удалить плагин/мод с сервера",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int, filename: str):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        
        # Получаем информацию о сервере
        server_data, status = await bot.api_request(f"/api/server/status/{port}")
        if status != 200:
            await interaction.followup.send(f"❌ Сервер на порту {port} не найден")
            return
            
        server_type = server_data.get('server_type', 'vanilla')
        
        # Определяем папку с плагинами/модами
        if server_type in ['paper', 'purpur']:
            folder = 'plugins'
        elif server_type == 'fabric':
            folder = 'mods'
        else:
            folder = 'plugins'
            
        # Формируем путь к файлу
        file_path = f"{folder}/{filename}"
        if not file_path.endswith('.jar'):
            file_path += '.jar'
            
        # Удаляем файл
        delete_data = {'path': file_path}
        result, delete_status = await bot.api_request(f"/server_files/{port}/delete", "POST", delete_data)
        
        if delete_status == 200:
            embed = discord.Embed(
                title="✅ Плагин/мод удален",
                description=f"Файл `{filename}` удален с сервера {port}",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка удаления: {result.get('error', 'Неизвестная ошибка')}")

class ServerPluginFromURLCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="server_plugin_url",
            description="Установить плагин/мод по прямой ссылке",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction, port: int, url: str):
        await interaction.response.defer(thinking=True)
        
        bot = interaction.client
        
        # Проверяем существование сервера
        server_data, status = await bot.api_request(f"/api/server/status/{port}")
        if status != 200:
            await interaction.followup.send(f"❌ Сервер на порту {port} не найден")
            return
            
        # Скачиваем файл
        filename, file_content = await bot.download_file(url)
        
        if not filename or not file_content:
            await interaction.followup.send("❌ Не удалось скачать файл. Проверьте URL и попробуйте снова.")
            return
            
        # Определяем папку назначения
        server_type = server_data.get('server_type', 'vanilla')
        if server_type in ['paper', 'purpur']:
            folder = 'plugins'
        elif server_type == 'fabric':
            folder = 'mods'
        else:
            folder = 'plugins'
            
        # Загружаем файл на сервер
        file_data = {
            'bytes': file_content,
            'filename': filename,
            'path': folder
        }
        
        result, upload_status = await bot.api_upload(f"/server_files/{port}/upload", file_data)
        
        if upload_status == 200:
            embed = discord.Embed(
                title="✅ Плагин/мод установлен!",
                description=(
                    f"**Файл:** {filename}\n"
                    f"**Сервер:** порт {port}\n"
                    f"**Папка:** {folder}\n\n"
                    "Для применения изменений может потребоваться перезагрузка сервера."
                ),
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(f"❌ Ошибка загрузки: {result.get('error', 'Неизвестная ошибка')}")

class HelpCommand(app_commands.Command):
    def __init__(self):
        super().__init__(
            name="help",
            description="Показать справку по командам",
            callback=self.callback,
            nsfw=False
        )
    
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 Minecraft Server Manager - Помощь",
            description="Список доступных команд для управления Minecraft серверами",
            color=discord.Color.blue()
        )
        
        commands_list = [
            ("/server_status <port>", "Показать статус сервера по указанному порту"),
            ("/server_start <port>", "Запустить сервер по указанному порту"),
            ("/server_stop <port>", "Остановить сервер по указанному порту"),
            ("/server_restart <port>", "Перезапустить сервер по указанному порту"),
            ("/server_list", "Показать список всех серверов"),
            ("/server_create", "Интерактивное создание нового сервера"),
            ("/server_logs <port>", "Показать последние логи сервера"),
            ("/server_command <port> <command>", "Выполнить команду на сервере"),
            ("/server_plugins <port>", "Показать список плагинов/модов сервера"),
            ("/server_install_plugin <port>", "Интерактивная установка плагина/мода"),
            ("/server_plugin_url <port> <url>", "Установить плагин/мод по прямой ссылке"),
            ("/server_remove_plugin <port> <filename>", "Удалить плагин/мод с сервера")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
            
        embed.set_footer(text=f"Префикс текстовых команд: {BOT_PREFIX}")
        await interaction.response.send_message(embed=embed)

def main():
    """Основная функция запуска бота"""
    if not DISCORD_TOKEN:
        print("❌ Ошибка: DISCORD_TOKEN не установлен в переменных окружения")
        print("Добавьте DISCORD_TOKEN=your_bot_token в файл .env")
        return
        
    bot = MinecraftBot()
    
    try:
        bot.run(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("❌ Ошибка: Неверный токен бота")
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")

if __name__ == "__main__":
    main()