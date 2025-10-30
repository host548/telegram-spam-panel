"""
FastAPI Server для Telegram Manager
Production версия API сервера для работы с веб-панелью
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import ssl
import os
from pathlib import Path
import uuid
import bcrypt
import jwt

# DB imports
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import JSON

# Импорт вашего Telegram модуля
from telegram_core import TelegramCoreManager

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

app = FastAPI(
    title="Telegram Manager API",
    version="2.0",
    description="API для управления Telegram рассылками"
)

# CORS настройки
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене замените на конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Создаем директорию для загрузок
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Telegram менеджер
telegram_manager = TelegramCoreManager()

# JWT настройки
JWT_SECRET = os.environ.get("JWT_SECRET", "bd801a3fcbd3f7a0a94e1a07b5073da71bc7db3674061b98f8f185b0cd81371a")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# Database настройки (Render Postgres)
DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://telegram_panel_user:I8nD92fSaRve81n7JUhcYptyszfZJEoj@dpg-d414kcpr0fns739ui7s0-a/telegram_panel")

# Конвертируем URL для asyncpg
if DATABASE_URL.startswith("postgresql://"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
else:
    ASYNC_DATABASE_URL = DATABASE_URL

# Убираем любые параметры SSL из URL если они есть
ASYNC_DATABASE_URL = ASYNC_DATABASE_URL.split('?')[0]

# Настройки SSL для Render
connect_args = {}
if "render.com" in DATABASE_URL or "dpg-" in DATABASE_URL:
    # Для Render нужно просто передать True, asyncpg сам настроит SSL
    connect_args["ssl"] = "require"

# Создаем engine с правильными параметрами SSL
engine = create_async_engine(
    ASYNC_DATABASE_URL, 
    echo=False, 
    pool_pre_ping=True,
    connect_args=connect_args
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Модели базы данных
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(primary_key=True)
    password_hash: Mapped[str] = mapped_column()
    user_id: Mapped[str] = mapped_column(unique=True)

class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default={})

class SessionInfo(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(primary_key=True)  # user_id:phone
    data: Mapped[dict] = mapped_column(JSON, default={})

# Инициализация БД
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logging.info("Database initialized successfully")

@app.on_event("startup")
async def startup():
    await init_db()
    await restore_sessions()
async def restore_sessions():
    """Восстановление активных сессий из БД при запуске"""
    try:
        async with async_session() as session:
            # Получаем всех пользователей
            result = await session.execute(select(User))
            users = result.scalars().all()
            
            for user in users:
                try:
                    user_data = await get_user_data(user.user_id, session)
                    accounts = user_data.get('accounts', {})
                    
                    for phone, account_info in accounts.items():
                        if account_info.get('authorized') and account_info.get('session_string'):
                            user_hash = int(user.user_id.replace('-', '')[:8], 16) if '-' in user.user_id else hash(user.user_id)
                            
                            # Восстанавливаем сессию
                            try:
                                await telegram_manager.create_session(
                                    user_hash, 
                                    phone, 
                                    account_info['session_string']
                                )
                                logging.info(f"Restored session for {phone}")
                            except Exception as e:
                                logging.error(f"Failed to restore session for {phone}: {e}")
                except Exception as e:
                    logging.error(f"Error restoring sessions for user {user.username}: {e}")
                    
        logging.info("Session restoration completed")
    except Exception as e:
        logging.error(f"Error in restore_sessions: {e}")

@app.on_event("shutdown")
async def shutdown():
    await engine.dispose()

# Dependency для получения DB сессии
async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

# Dependency для получения текущего пользователя
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        token = token.replace("Bearer ", "")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail="Invalid token")

# Pydantic модели для запросов
class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class AuthStartRequest(BaseModel):
    phone: str

class AuthCodeRequest(BaseModel):
    code: str
    phone_code_hash: str

class InstantSettingsRequest(BaseModel):
    account_phone: str
    enabled: bool
    template_name: Optional[str] = None
    delay_seconds: int = 30

# Helper функции для работы с данными пользователя
async def get_user_data(user_id: str, db: AsyncSession) -> dict:
    """Получение данных пользователя из БД"""
    try:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        settings = result.scalar_one_or_none()
        
        if settings and settings.data:
            return settings.data
        
        # Возвращаем дефолтную структуру
        return {
            'accounts': {},
            'templates': {},
            'stats': {'sent': 0, 'success': 0, 'failed': 0},
            'history': [],
            'instant_settings': {}
        }
    except SQLAlchemyError as e:
        logging.error(f"Error getting user data: {e}")
        return {
            'accounts': {},
            'templates': {},
            'stats': {'sent': 0, 'success': 0, 'failed': 0},
            'history': [],
            'instant_settings': {}
        }

async def update_user_data(user_id: str, updates: dict, db: AsyncSession):
    """Обновление данных пользователя в БД"""
    try:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        settings = result.scalar_one_or_none()
        
        if settings:
            # Объединяем существующие данные с обновлениями
            current_data = settings.data or {}
            merged_data = {**current_data, **updates}
            settings.data = merged_data
        else:
            # Создаем новую запись
            settings = UserSettings(user_id=user_id, data=updates)
            db.add(settings)
        
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Error updating user data: {e}")
        raise HTTPException(status_code=500, detail="Database error")

async def save_session_info(user_id: str, phone: str, session_data: dict, db: AsyncSession):
    """Сохранение информации о сессии авторизации"""
    session_id = f"{user_id}:{phone}"
    try:
        result = await db.execute(select(SessionInfo).where(SessionInfo.id == session_id))
        session = result.scalar_one_or_none()
        
        if session:
            session.data = session_data
        else:
            session = SessionInfo(id=session_id, data=session_data)
            db.add(session)
        
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"Error saving session info: {e}")

async def get_session_info(user_id: str, phone: str, db: AsyncSession) -> dict:
    """Получение информации о сессии авторизации"""
    session_id = f"{user_id}:{phone}"
    try:
        result = await db.execute(select(SessionInfo).where(SessionInfo.id == session_id))
        session = result.scalar_one_or_none()
        return session.data if session else {}
    except SQLAlchemyError:
        return {}

# ==================== API ENDPOINTS ====================

@app.get("/", response_class=HTMLResponse)
async def serve_web_panel():
    """Главная страница - веб-панель"""
    try:
        panel_path = Path("web_panel.html")
        if panel_path.exists():
            with open(panel_path, "r", encoding="utf-8") as f:
                content = f.read()
            return HTMLResponse(content=content)
        else:
            return HTMLResponse(content="<h1>Panel file not found</h1>", status_code=404)
    except Exception as e:
        logging.error(f"Error serving web panel: {e}")
        raise HTTPException(status_code=500, detail="Error loading panel")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "2.0",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/api/register")
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Регистрация нового пользователя"""
    try:
        # Проверяем существование пользователя
        result = await db.execute(select(User).where(User.username == request.username))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Username already exists")
        
        # Генерируем хэш пароля
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(request.password.encode('utf-8'), salt).decode('utf-8')
        user_id = str(uuid.uuid4())
        
        # Создаем пользователя
        new_user = User(
            username=request.username,
            password_hash=password_hash,
            user_id=user_id
        )
        db.add(new_user)
        
        # Создаем начальные настройки пользователя
        initial_settings = UserSettings(
            user_id=user_id,
            data={
                'accounts': {},
                'templates': {},
                'stats': {'sent': 0, 'success': 0, 'failed': 0},
                'history': [],
                'instant_settings': {}
            }
        )
        db.add(initial_settings)
        
        await db.commit()
        
        logging.info(f"New user registered: {request.username}")
        return {
            "success": True,
            "message": "Registration successful",
            "user_id": user_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logging.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail="Registration failed")

@app.post("/api/login")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Авторизация пользователя"""
    try:
        # Находим пользователя
        result = await db.execute(select(User).where(User.username == request.username))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=400, detail="Invalid credentials")
        
        # Проверяем пароль
        if not bcrypt.checkpw(request.password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=400, detail="Invalid credentials")
        
        # Создаем JWT токен
        expiration = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
        token_data = {
            "user_id": user.user_id,
            "username": user.username,
            "exp": expiration
        }
        token = jwt.encode(token_data, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        logging.info(f"User logged in: {request.username}")
        return {
            "success": True,
            "token": token,
            "user_id": user.user_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="Login failed")

@app.get("/api/accounts")
async def get_accounts(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение списка подключенных аккаунтов"""
    try:
        user_data = await get_user_data(user_id, db)
        accounts = user_data.get('accounts', {})
        
        accounts_list = []
        for phone, info in accounts.items():
            accounts_list.append({
                "phone": phone,
                "authorized": info.get('authorized', False),
                "added_at": info.get('added_at', ''),
                "chats": info.get('chats', 0),
                "sent": info.get('sent', 0)
            })
        
        return {
            "success": True,
            "accounts": accounts_list,
            "total": len(accounts_list)
        }
    except Exception as e:
        logging.error(f"Error getting accounts: {e}")
        raise HTTPException(status_code=500, detail="Failed to get accounts")

@app.post("/api/auth/start")
async def start_auth(request: AuthStartRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Начало процесса авторизации Telegram аккаунта"""
    try:
        # Создаем хэш для пользователя
        user_hash = int(user_id.replace('-', '')[:8], 16) if '-' in user_id else hash(user_id)
        
        # Получаем существующую сессию из БД если есть
        session_info = await get_session_info(user_id, request.phone, db)
        session_string = session_info.get('session_string') if session_info else None
        
        # Создаем сессию Telegram
        userbot = await telegram_manager.create_session(user_hash, request.phone, session_string)
        
        # Отправляем код авторизации
        phone_code_hash = await userbot.send_code()  # Изменено с send_code
        
        # Сохраняем информацию о сессии
        await save_session_info(
            user_id,
            request.phone,
            {
                'phone_code_hash': phone_code_hash,
                'phone': request.phone,
                'timestamp': datetime.now().isoformat()
            },
            db
        )
        
        logging.info(f"Auth code sent to {request.phone}")
        return {
            "success": True,
            "phone_code_hash": phone_code_hash,
            "message": "Code sent to Telegram"
        }
        
    except Exception as e:
        logging.error(f"Auth start error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/code")
async def submit_auth_code(request: AuthCodeRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Подтверждение кода авторизации"""
    try:
        # Получаем сессию
        user_hash = int(user_id.replace('-', '')[:8], 16) if '-' in user_id else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Авторизуемся с кодом
        result = await userbot.sign_in(request.code, request.phone_code_hash)
        
        if not result.get('success'):
            if result.get('needs_password'):
                raise HTTPException(status_code=400, detail="2FA password required")
            raise HTTPException(status_code=400, detail=result.get('error', 'Invalid code'))
        
        # ВАЖНО: Получаем session_string для сохранения
        session_string = result.get('session_string') or userbot.get_session_string()
        
        # Получаем информацию о телефоне из сессии
        session_info = await get_session_info(user_id, "", db)
        phone = session_info.get('phone', userbot.phone if hasattr(userbot, 'phone') else "")
        
        # Сохраняем session string для восстановления после перезапуска
        await save_session_info(
            user_id,
            phone,
            {
                'session_string': session_string,
                'phone': phone,
                'timestamp': datetime.now().isoformat()
            },
            db
        )
        
        # Обновляем информацию об аккаунте
        user_data = await get_user_data(user_id, db)
        
        if 'accounts' not in user_data:
            user_data['accounts'] = {}
        
        # Получаем количество диалогов
        try:
            dialogs = await userbot.get_dialogs()
            chats_count = len(dialogs)
        except:
            chats_count = 0
        
        user_data['accounts'][phone] = {
            'authorized': True,
            'added_at': datetime.now().isoformat(),
            'chats': chats_count,
            'sent': 0,
            'session_string': session_string  # Сохраняем для восстановления
        }
        
        await update_user_data(user_id, {'accounts': user_data['accounts']}, db)
        
        logging.info(f"Account authorized: {phone}")
        return {
            "success": True,
            "message": "Account authorized successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Auth code error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/accounts/{phone}")
async def delete_account(phone: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Удаление подключенного аккаунта"""
    try:
        user_data = await get_user_data(user_id, db)
        
        if phone not in user_data.get('accounts', {}):
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Удаляем аккаунт из данных
        del user_data['accounts'][phone]
        await update_user_data(user_id, {'accounts': user_data['accounts']}, db)
        
        # Отключаем Telegram сессию
        try:
            user_hash = int(user_id.replace('-', '')[:8], 16) if '-' in user_id else hash(user_id)
            userbot = telegram_manager.get_session(user_hash)
            if userbot:
                await userbot.disconnect()
        except:
            pass
        
        logging.info(f"Account deleted: {phone}")
        return {"success": True, "message": "Account deleted"}
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Delete account error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete account")

@app.get("/api/accounts/{phone}/contacts")
async def get_contacts(phone: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение списка контактов/чатов аккаунта"""
    try:
        user_hash = int(user_id.replace('-', '')[:8], 16) if '-' in user_id else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Получаем все диалоги
        all_dialogs = await userbot.get_dialogs()
        
        contacts = []
        for dialog in all_dialogs:
            # Определяем тип чата
            if dialog.get('type') == 'user':
                contact_type = 'private'
            elif dialog.get('type') == 'chat':
                contact_type = 'group'
            else:
                contact_type = 'channel'
            
            name = dialog.get('name', 'Unknown')
            
            contacts.append({
                "id": dialog['id'],
                "name": name,
                "username": dialog.get('username', ''),
                "type": contact_type,
                "avatar": name[0].upper() if name else '?'
            })
        
        return {
            "success": True,
            "contacts": contacts,
            "total": len(contacts)
        }
        
    except Exception as e:
        logging.error(f"Get contacts error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/templates")
async def get_templates(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение списка шаблонов"""
    try:
        user_data = await get_user_data(user_id, db)
        templates = user_data.get('templates', {})
        
        templates_list = []
        for name, info in templates.items():
            templates_list.append({
                "name": name,
                "text": info.get('text', ''),
                "media_type": info.get('media_type'),
                "created_at": info.get('created_at', '')
            })
        
        return {
            "success": True,
            "templates": templates_list,
            "total": len(templates_list)
        }
    except Exception as e:
        logging.error(f"Get templates error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get templates")

@app.post("/api/templates/create")
async def create_template(
    name: str = Form(...),
    text: str = Form(...),
    media_type: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Создание нового шаблона"""
    try:
        user_data = await get_user_data(user_id, db)
        
        if 'templates' not in user_data:
            user_data['templates'] = {}
        
        if name in user_data['templates']:
            raise HTTPException(status_code=400, detail="Template with this name already exists")
        
        # Сохраняем файл если есть
        file_path = None
        if file:
            file_extension = Path(file.filename).suffix
            file_name = f"{uuid.uuid4()}{file_extension}"
            file_path = UPLOAD_DIR / file_name
            
            content = await file.read()
            with open(file_path, "wb") as f:
                f.write(content)
        
        # Создаем шаблон
        user_data['templates'][name] = {
            'text': text,
            'media_type': media_type,
            'file_path': str(file_path) if file_path else None,
            'created_at': datetime.now().isoformat()
        }
        
        await update_user_data(user_id, {'templates': user_data['templates']}, db)
        
        logging.info(f"Template created: {name}")
        return {
            "success": True,
            "message": f"Template '{name}' created successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Create template error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create template")

@app.delete("/api/templates/{template_name}")
async def delete_template(template_name: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Удаление шаблона"""
    try:
        user_data = await get_user_data(user_id, db)
        
        if template_name not in user_data.get('templates', {}):
            raise HTTPException(status_code=404, detail="Template not found")
        
        # Удаляем файл если есть
        template_info = user_data['templates'][template_name]
        if template_info.get('file_path'):
            try:
                file_path = Path(template_info['file_path'])
                if file_path.exists():
                    file_path.unlink()
            except:
                pass
        
        # Удаляем шаблон
        del user_data['templates'][template_name]
        await update_user_data(user_id, {'templates': user_data['templates']}, db)
        
        logging.info(f"Template deleted: {template_name}")
        return {
            "success": True,
            "message": f"Template '{template_name}' deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Delete template error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete template")

@app.post("/api/broadcast")
async def broadcast_message(
    account_phone: str = Form(...),
    text: str = Form(...),
    delay_seconds: int = Form(30),
    chat_ids: Optional[str] = Form(None),  # Приходит как строка с ID через запятую
    file: Optional[UploadFile] = File(None),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Запуск рассылки сообщений"""
    try:
        user_hash = int(user_id.replace('-', '')[:8], 16) if '-' in user_id else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Получаем все диалоги
        all_dialogs = await userbot.get_dialogs()
        
        # Фильтруем диалоги если указаны конкретные чаты
        if chat_ids:
            # Парсим ID из строки
            selected_ids = [int(id.strip()) for id in chat_ids.split(',') if id.strip()]
            dialogs = [d for d in all_dialogs if d['id'] in selected_ids]
        else:
            dialogs = all_dialogs
        
        if not dialogs:
            raise HTTPException(status_code=400, detail="No chats selected for broadcast")
        
        # Сохраняем файл если есть
        file_path = None
        if file:
            file_extension = Path(file.filename).suffix
            file_name = f"{uuid.uuid4()}{file_extension}"
            file_path = UPLOAD_DIR / file_name
            
            content = await file.read()
            with open(file_path, "wb") as f:
                f.write(content)
        
        # Запускаем рассылку
        schedule_time = datetime.now() + timedelta(seconds=delay_seconds)
        successful, failed = await userbot.broadcast_message(
            dialogs,
            text,
            schedule_time,
            str(file_path) if file_path else None
        )
        
        # Обновляем статистику
        user_data = await get_user_data(user_id, db)
        
        # Обновляем общую статистику
        stats = user_data.get('stats', {'sent': 0, 'success': 0, 'failed': 0})
        stats['sent'] += len(dialogs)
        stats['success'] += successful
        stats['failed'] += failed
        
        # Обновляем статистику аккаунта
        if 'accounts' in user_data and account_phone in user_data['accounts']:
            user_data['accounts'][account_phone]['sent'] = user_data['accounts'][account_phone].get('sent', 0) + len(dialogs)
        
        # Добавляем запись в историю
        history = user_data.get('history', [])
        history.append({
            'date': datetime.now().isoformat(),
            'account_phone': account_phone,
            'total': len(dialogs),
            'successful': successful,
            'failed': failed,
            'text': text[:100] + '...' if len(text) > 100 else text
        })
        
        # Ограничиваем историю последними 100 записями
        if len(history) > 100:
            history = history[-100:]
        
        # Сохраняем обновления
        await update_user_data(user_id, {
            'stats': stats,
            'history': history,
            'accounts': user_data.get('accounts', {})
        }, db)
        
        # Удаляем временный файл
        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except:
                pass
        
        logging.info(f"Broadcast completed: {successful}/{len(dialogs)} successful")
        return {
            "success": True,
            "total": len(dialogs),
            "successful": successful,
            "failed": failed,
            "schedule_time": schedule_time.isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Broadcast error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/instant/settings")
async def save_instant_settings(
    request: InstantSettingsRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Сохранение настроек автоматических ответов"""
    try:
        user_data = await get_user_data(user_id, db)
        
        if 'instant_settings' not in user_data:
            user_data['instant_settings'] = {}
        
        user_data['instant_settings'][request.account_phone] = {
            'enabled': request.enabled,
            'template_name': request.template_name,
            'delay_seconds': request.delay_seconds,
            'updated_at': datetime.now().isoformat()
        }
        
        await update_user_data(user_id, {'instant_settings': user_data['instant_settings']}, db)
        
        logging.info(f"Instant settings updated for {request.account_phone}")
        return {
            "success": True,
            "message": "Settings saved successfully"
        }
        
    except Exception as e:
        logging.error(f"Save instant settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings")

@app.get("/api/instant/settings/{phone}")
async def get_instant_settings(phone: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение настроек автоматических ответов для аккаунта"""
    try:
        user_data = await get_user_data(user_id, db)
        
        settings = user_data.get('instant_settings', {}).get(phone, {
            'enabled': False,
            'template_name': None,
            'delay_seconds': 30
        })
        
        return {
            "success": True,
            "settings": settings
        }
        
    except Exception as e:
        logging.error(f"Get instant settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get settings")

@app.get("/api/stats")
async def get_stats(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение общей статистики"""
    try:
        user_data = await get_user_data(user_id, db)
        
        stats = user_data.get('stats', {'sent': 0, 'success': 0, 'failed': 0})
        
        return {
            "success": True,
            "accounts": len(user_data.get('accounts', {})),
            "templates": len(user_data.get('templates', {})),
            "sent": stats.get('sent', 0),
            "success": stats.get('success', 0)
        }
        
    except Exception as e:
        logging.error(f"Get stats error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")

@app.get("/api/history")
async def get_history(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Получение истории рассылок"""
    try:
        user_data = await get_user_data(user_id, db)
        history = user_data.get('history', [])
        
        # Возвращаем последние 50 записей в обратном порядке (новые сверху)
        recent_history = history[-50:] if len(history) > 50 else history
        recent_history.reverse()
        
        return {
            "success": True,
            "history": recent_history,
            "total": len(history)
        }
        
    except Exception as e:
        logging.error(f"Get history error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get history")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)





