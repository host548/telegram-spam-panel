"""
Telegram Core Module
Вся логика работы с Telethon (авторизация, получение чатов, рассылка)
"""

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, 
    FloodWaitError, 
    AuthKeyUnregisteredError, 
    UserDeactivatedError
)
from datetime import datetime
import asyncio
import logging
import os

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Конфигурация
API_ID = 21127926
API_HASH = "71bcb9a4f312f9e38083548e225bc708"
MAX_CONCURRENT_SENDS = 10
SEND_DELAY = 0.1


class TelegramUserbot:
    """Класс для работы с userbot через Telethon"""
    
    def __init__(self, user_id: int, phone: str):
        self.user_id = user_id
        self.phone = phone
        self.session_name = f"sessions/session_{user_id}"
        self.client = None
        self.is_authorized = False
        self.lock = asyncio.Lock()
    
    async def disconnect(self):
        """Корректное отключение клиента"""
        async with self.lock:
            try:
                if self.client and self.client.is_connected():
                    await self.client.disconnect()
                    await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"Ошибка при отключении: {e}")
            finally:
                self.client = None
                self.is_authorized = False
    
    async def create_client(self, session_string: str = None):
        """Создание и подключение Telethon клиента (с поддержкой session_string)"""
        os.makedirs("sessions", exist_ok=True)
        
        async with self.lock:
            if self.client:
                try:
                    if self.client.is_connected():
                        await self.client.disconnect()
                    await asyncio.sleep(0.5)
                except:
                    pass
            
            # Если session_string передан, используем его (для восстановления)
            if session_string:
                self.client = TelegramClient(
                    StringSession(session_string), 
                    api_id=API_ID,
                    api_hash=API_HASH,
                    auto_reconnect=True,
                    connection_retries=5,
                    timeout=30,
                    flood_sleep_threshold=60
                )
            else:
                # Иначе используем файловую сессию
                self.client = TelegramClient(
                    self.session_name,
                    api_id=API_ID,
                    api_hash=API_HASH,
                    auto_reconnect=True,
                    connection_retries=5,
                    timeout=30,
                    flood_sleep_threshold=60
                )
            
            try:
                await self.client.connect()
                logging.info(f"Client created and connected: {type(self.client)}")  # Лог для отладки
            except Exception as e:
                logging.error(f"Ошибка подключения: {e}")
                # Обработка повреждённой базы данных (только для файловой сессии)
                if not session_string and ("database" in str(e).lower() or "locked" in str(e).lower()):
                    try:
                        await self.client.disconnect()
                    except:
                        pass
                    
                    session_file = f"{self.session_name}.session"
                    if os.path.exists(session_file):
                        try:
                            os.remove(session_file)
                            logging.info(f"Удалён повреждённый файл: {session_file}")
                        except:
                            pass
                    
                    await asyncio.sleep(1)
                    self.client = TelegramClient(
                        self.session_name,
                        api_id=API_ID,
                        api_hash=API_HASH,
                        auto_reconnect=True,
                        connection_retries=5,
                        timeout=30,
                        flood_sleep_threshold=60
                    )
                    await self.client.connect()
                    logging.info(f"New client created after cleanup: {type(self.client)}")  # Лог
                    
            return self.client
    
    async def check_session(self):
        """Проверка валидности сессии"""
        async with self.lock:
            try:
                if not self.client:
                    return False
                    
                if not self.client.is_connected():
                    await self.client.connect()
                    
                me = await self.client.get_me()
                self.is_authorized = bool(me)
                return bool(me)
            except (AuthKeyUnregisteredError, UserDeactivatedError, ConnectionError):
                self.is_authorized = False
                return False
            except Exception as e:
                logging.error(f"Ошибка проверки сессии: {e}")
                self.is_authorized = False
                return False
    
    async def send_code(self):
        """Отправка кода авторизации"""
        async with self.lock:
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                
                logging.info(f"Type of self.client before send_code_request: {type(self.client)}")  # Лог для отладки
                if not hasattr(self.client, 'send_code_request'):
                    logging.error("self.client does not have 'send_code_request' method! Check Telethon installation.")
                    raise AttributeError("TelegramClient missing 'send_code_request' method")
                
                result = await self.client.send_code_request(self.phone)
                return result.phone_code_hash
            except Exception as e:
                if "DC" in str(e) or "migrate" in str(e).lower():
                    logging.info(f"Миграция на другой DC для {self.phone}")
                    await self.client.disconnect()
                    await asyncio.sleep(1)
                    await self.client.connect()
                    result = await self.client.send_code_request(self.phone)
                    return result.phone_code_hash
                logging.error(f"Error in send_code: {e}")
                raise e
    
    async def sign_in(self, phone_code: str, phone_code_hash: str):
        """Авторизация по коду"""
        async with self.lock:
            try:
                await self.client.sign_in(
                    self.phone, 
                    phone_code, 
                    phone_code_hash=phone_code_hash
                )
                self.is_authorized = True
                return {"success": True, "needs_password": False}
            except SessionPasswordNeededError:
                return {"success": False, "needs_password": True}
            except Exception as e:
                self.is_authorized = False
                return {"success": False, "error": str(e)}
    
    async def check_password(self, password: str):
        """Проверка 2FA пароля"""
        async with self.lock:
            try:
                await self.client.sign_in(password=password)
                self.is_authorized = True
                return True
            except:
                self.is_authorized = False
                return False
    
    async def get_dialogs(self):
        """Получение списка чатов/групп/каналов"""
        if not await self.check_session():
            raise Exception("Сессия недействительна")
        
        dialogs = []
        try:
            logging.info(f"Загрузка диалогов для user {self.user_id}")
            
            async def load_dialogs():
                count = 0
                async for dialog in self.client.iter_dialogs(limit=200):
                    try:
                        chat = dialog.entity
                        
                        if not chat:
                            continue
                        
                        # Определяем тип и имя чата
                        if hasattr(chat, 'first_name'):
                            chat_type = 'private'
                            name = chat.first_name or "Без имени"
                            if hasattr(chat, 'last_name') and chat.last_name:
                                name += f" {chat.last_name}"
                        elif hasattr(chat, 'title'):
                            chat_type = 'channel' if (hasattr(chat, 'broadcast') and chat.broadcast) else 'group'
                            name = chat.title
                        else:
                            logging.warning(f"Неизвестный тип чата: {type(chat)}")
                            continue
                        
                        dialogs.append({
                            'id': dialog.id,
                            'name': name,
                            'username': getattr(chat, 'username', None),
                            'type': chat_type
                        })
                        
                        count += 1
                        if count % 10 == 0:
                            logging.info(f"Загружено {count} диалогов...")
                            
                    except Exception as e:
                        logging.warning(f"Ошибка обработки диалога: {e}")
                        continue
                
                logging.info(f"Загрузка завершена. Всего: {len(dialogs)}")
                return dialogs
            
            # Таймаут 30 секунд
            dialogs = await asyncio.wait_for(load_dialogs(), timeout=30.0)
            
        except asyncio.TimeoutError:
            logging.error("Таймаут при загрузке диалогов (30 сек)")
            raise Exception("Превышено время ожидания загрузки диалогов")
        except Exception as e:
            logging.error(f"Ошибка загрузки диалогов: {e}")
            raise Exception(f"Не удалось загрузить диалоги: {e}")
            
        return dialogs
    
    async def schedule_message(
        self, 
        chat_id: int, 
        text: str, 
        schedule_datetime: datetime,
        media_type: str = None,
        file_path: str = None,
        original_filename: str = None
    ):
        """Отправка запланированного сообщения"""
        async with self.lock:
            if not await self.check_session():
                raise Exception("Сессия недействительна")
            
            try:
                if media_type and file_path:
                    attributes = None
                    if original_filename:
                        from telethon.tl.types import DocumentAttributeFilename
                        attributes = [DocumentAttributeFilename(original_filename)]
                    
                    await self.client.send_file(
                        chat_id, 
                        file_path,
                        caption=text or "",
                        schedule=schedule_datetime,
                        force_document=(media_type == "document"),
                        attributes=attributes
                    )
                else:
                    if not text or not text.strip():
                        return False
                    await self.client.send_message(
                        chat_id, 
                        text, 
                        schedule=schedule_datetime
                    )
                return True
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                return False
            except (AuthKeyUnregisteredError, UserDeactivatedError):
                self.is_authorized = False
                raise Exception("Сессия отключена")
            except Exception as e:
                logging.error(f"Ошибка отправки: {e}")
                return False
    
    async def send_to_chat(
        self, 
        dialog: dict, 
        text: str, 
        schedule_datetime: datetime, 
        semaphore: asyncio.Semaphore
    ):
        """Отправка сообщения в один чат (для массовой рассылки)"""
        async with semaphore:
            try:
                await self.client.send_message(
                    dialog['id'], 
                    text, 
                    schedule=schedule_datetime
                )
                await asyncio.sleep(SEND_DELAY)
                return True, None
            except FloodWaitError as e:
                return False, f"FloodWait {e.seconds}s"
            except Exception as e:
                return False, str(e)
    
    async def broadcast_message(
        self, 
        dialogs: list, 
        text: str, 
        schedule_datetime: datetime,
        progress_callback=None
    ):
        """Массовая рассылка сообщений"""
        if not await self.check_session():
            raise Exception("Сессия недействительна")
        
        successful = failed = 0
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)
        
        tasks = [
            (dialog, self.send_to_chat(dialog, text, schedule_datetime, semaphore)) 
            for dialog in dialogs
        ]
        
        for idx, (dialog, task) in enumerate(tasks, 1):
            try:
                success, error = await task
                if success:
                    successful += 1
                else:
                    failed += 1
                
                if progress_callback:
                    await progress_callback(idx, len(dialogs), successful, failed)
            except:
                failed += 1
        
        return successful, failed


class TelegramCoreManager:
    """Менеджер для управления userbot сессиями"""
    
    def __init__(self):
        self.sessions = {}  # {user_id: TelegramUserbot}
    
    async def create_session(self, user_id: int, phone: str, session_string: str = None):
        """Создать новую сессию (с опциональным session_string для восстановления)"""
        if user_id in self.sessions:
            await self.sessions[user_id].disconnect()
        
        userbot = TelegramUserbot(user_id, phone)
        await userbot.create_client(session_string)  # Передаём session_string сюда
        self.sessions[user_id] = userbot
        return userbot
    
    def get_session(self, user_id: int):
        """Получить существующую сессию"""
        return self.sessions.get(user_id)
    
    async def remove_session(self, user_id: int):
        """Удалить сессию"""
        if user_id in self.sessions:
            await self.sessions[user_id].disconnect()
            del self.sessions[user_id]
            
            # Удалить файл сессии
            session_file = f"sessions/session_{user_id}.session"
            if os.path.exists(session_file):
                try:
                    os.remove(session_file)
                except:
                    pass
    
    async def cleanup_all(self):
        """Очистить все сессии"""
        for user_id in list(self.sessions.keys()):
            await self.remove_session(user_id)
