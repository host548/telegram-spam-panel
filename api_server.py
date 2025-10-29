"""
FastAPI Server для Telegram Manager
API сервер для работы с веб-панелью, с интеграцией Render Postgres вместо JSON.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import json
import os
from pathlib import Path
import uuid
import bcrypt
import jwt  # PyJWT

# DB imports
from sqlalchemy import Column, String, JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.exc import SQLAlchemyError

from telegram_core import TelegramCoreManager  # Предполагаю, это ваш модуль

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

app = FastAPI(
    title="Telegram Manager API",
    version="2.0",
    description="API для управления Telegram рассылками"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Уточните для prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Telegram менеджер
telegram_manager = TelegramCoreManager()

# JWT
JWT_SECRET = os.environ.get("JWT_SECRET", "bd801a3fcbd3f7a0a94e1a07b5073da71bc7db3674061b98f8f185b0cd81371a")
JWT_ALGORITHM = "HS256"

# DB setup (Render Postgres)
DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://telegram_panel_user:I8nD92fSaRve81n7JUhcYptyszfZJEoj@dpg-d414kcpr0fns739ui7s0-a/telegram_panel?sslmode=require")
# Fix for async driver
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)  # echo для debug, удалите в prod
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

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
    data: Mapped[dict] = mapped_column(JSON)  # Settings как JSON

class SessionInfo(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(primary_key=True)  # user_id:phone
    data: Mapped[dict] = mapped_column(JSON)  # phone_code_hash etc.

# Инициализация DB (создаёт таблицы если не существуют)
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.on_event("startup")
async def startup():
    await init_db()

# DB зависимость
async def get_db():
    async with async_session() as session:
        yield session

# JWT зависимость
async def get_current_user(request: Request):
    token = request.headers.get("Authorization")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token.replace("Bearer ", ""), JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload["user_id"]
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

# Модели запросов
class RegisterRequest(BaseModel):
    username: str
    password: str

class LoginRequest(BaseModel):
    username: str
    password: str

class AuthStartRequest(BaseModel):
    phone: str  # user_id из Depends

class AuthCodeRequest(BaseModel):
    code: str
    phone_code_hash: str  # user_id из Depends

class AuthPasswordRequest(BaseModel):
    password: str  # user_id из Depends

class AccountInfo(BaseModel):
    phone: str
    status: str = "active"

class BroadcastRequest(BaseModel):
    account_phone: str
    text: str
    delay_seconds: int = 30
    chat_ids: Optional[List[int]] = None
    file_path: Optional[str] = None

class TemplateCreate(BaseModel):
    name: str
    text: str
    media_type: Optional[str] = None
    file_path: Optional[str] = None

class InstantSettingsRequest(BaseModel):
    account_phone: str
    enabled: bool
    template_name: Optional[str] = None
    delay_seconds: int = 30

# Главная страница
@app.get("/", response_class=HTMLResponse)
async def serve_web_panel():
    try:
        panel_path = Path("web_panel.html")
        if panel_path.exists():
            return FileResponse(panel_path)
        else:
            return HTMLResponse(
                content="""<html><head><title>Telegram Manager</title><style>body { font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; } .container { text-align: center; padding: 40px; background: rgba(255,255,255,0.1); border-radius: 20px; backdrop-filter: blur(10px); } h1 { margin: 0 0 20px 0; } a { color: #fff; text-decoration: underline; }</style></head><body><div class="container"><h1>🚀 Telegram Manager API</h1><p>API сервер успешно запущен!</p><p>📖 Документация: <a href="/docs">/docs</a></p><p>💚 Статус: <a href="/health">/health</a></p></div></body></html>""",
                status_code=200
            )
    except Exception as e:
        logging.error(f"Ошибка загрузки панели: {e}")
        raise HTTPException(status_code=500, detail="Ошибка загрузки панели")

# Health check
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "Telegram Manager API",
        "version": "2.0",
        "timestamp": datetime.now().isoformat()
    }

# Регистрация
@app.post("/api/register")
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        existing = await db.execute(select(User).where(User.username == request.username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Username exists")
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(request.password.encode('utf-8'), salt).decode('utf-8')
        user_id = str(uuid.uuid4())
        new_user = User(username=request.username, password_hash=hashed, user_id=user_id)
        db.add(new_user)
        await db.commit()
        return {"success": True, "message": "Registered", "user_id": user_id}
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"DB error in register: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# Логин
@app.post("/api/login")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        user_query = await db.execute(select(User).where(User.username == request.username))
        user = user_query.scalar_one_or_none()
        if not user or not bcrypt.checkpw(request.password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=400, detail="Invalid credentials")
        token = jwt.encode({"user_id": user.user_id, "exp": datetime.utcnow() + timedelta(hours=24)}, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return {"success": True, "token": token, "user_id": user.user_id}
    except SQLAlchemyError as e:
        logging.error(f"DB error in login: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# Get user data
async def get_user_data(user_id: str, db: AsyncSession) -> dict:
    try:
        result = await db.execute(select(UserSettings.data).where(UserSettings.user_id == user_id))
        data = result.scalar()
        return data if data else {}
    except SQLAlchemyError as e:
        logging.error(f"DB error get_user_data: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# Update user data
async def update_user_data(user_id: str, new_data: dict, db: AsyncSession):
    try:
        settings_query = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        settings_obj = settings_query.scalar_one_or_none()
        if settings_obj:
            settings_obj.data = {**settings_obj.data, **new_data}
        else:
            settings_obj = UserSettings(user_id=user_id, data=new_data)
            db.add(settings_obj)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logging.error(f"DB error update_user_data: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# Save session info
async def save_session_info(user_id: str, phone: str, phone_code_hash: str, db: AsyncSession):
    session_id = f"{user_id}:{phone}"
    data = {
        'phone_code_hash': phone_code_hash,
        'timestamp': datetime.now().isoformat()
    }
    try:
        session_query = await db.execute(select(SessionInfo).where(SessionInfo.id == session_id))
        obj = session_query.scalar_one_or_none()
        if obj:
            obj.data = data
        else:
            obj = SessionInfo(id=session_id, data=data)
            db.add(obj)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        raise

# Get session info
async def get_session_info(user_id: str, phone: str, db: AsyncSession) -> dict:
    session_id = f"{user_id}:{phone}"
    try:
        result = await db.execute(select(SessionInfo.data).where(SessionInfo.id == session_id))
        return result.scalar() or {}
    except SQLAlchemyError:
        return {}

# Auth start
@app.post("/api/auth/start")
async def start_auth(request: AuthStartRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
        userbot = await telegram_manager.create_session(user_hash, request.phone)
        phone_code_hash = await userbot.send_code()
        await save_session_info(user_id, request.phone, phone_code_hash, db)
        return {"success": True, "phone_code_hash": phone_code_hash, "message": f"Код отправлен на {request.phone}"}
    except Exception as e:
        logging.error(f"Ошибка auth/start: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка отправки кода: {str(e)}")

# Verify code
@app.post("/api/auth/verify-code")
async def verify_code(request: AuthCodeRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена. Начните авторизацию заново")
        result = await userbot.sign_in(request.code, request.phone_code_hash)
        if result.get("success"):
            me = await userbot.client.get_me()
            user_data = await get_user_data(user_id, db)
            if 'accounts' not in user_data:
                user_data['accounts'] = {}
            user_data['accounts'][me.phone] = {
                'status': 'active',
                'username': me.username or 'Нет username',
                'first_name': me.first_name or '',
                'auth_date': datetime.now().isoformat()
            }
            await update_user_data(user_id, user_data, db)
            return {
                "success": True,
                "message": "Авторизация успешна!",
                "account": {
                    "phone": me.phone,
                    "username": me.username,
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip()
                }
            }
        elif result.get("needs_password"):
            return {"success": False, "needs_password": True, "message": "Требуется 2FA пароль"}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Неверный код"))
    except Exception as e:
        logging.error(f"Ошибка verify-code: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Verify password
@app.post("/api/auth/verify-password")
async def verify_password(request: AuthPasswordRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        success = await userbot.check_password(request.password)
        if success:
            me = await userbot.client.get_me()
            user_data = await get_user_data(user_id, db)
            if 'accounts' not in user_data:
                user_data['accounts'] = {}
            user_data['accounts'][me.phone] = {
                'status': 'active',
                'username': me.username or 'Нет username',
                'first_name': me.first_name or '',
                'auth_date': datetime.now().isoformat()
            }
            await update_user_data(user_id, user_data, db)
            return {
                "success": True,
                "message": "Авторизация с 2FA успешна!",
                "account": {
                    "phone": me.phone,
                    "username": me.username,
                    "name": f"{me.first_name or ''} {me.last_name or ''}".strip()
                }
            }
        else:
            raise HTTPException(status_code=400, detail="Неверный пароль")
    except Exception as e:
        logging.error(f"Ошибка verify-password: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Get accounts
@app.get("/api/accounts")
async def get_accounts(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        accounts = user_data.get('accounts', {})
        return {
            "success": True,
            "accounts": [
                {
                    "phone": phone,
                    "status": info.get('status', 'unknown'),
                    "username": info.get('username', ''),
                    "first_name": info.get('first_name', ''),
                    "auth_date": info.get('auth_date', '')
                }
                for phone, info in accounts.items()
            ],
            "total": len(accounts)
        }
    except Exception as e:
        logging.error(f"Ошибка get_accounts: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Delete account
@app.delete("/api/accounts/{phone}")
async def delete_account(phone: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        if phone in user_data.get('accounts', {}):
            del user_data['accounts'][phone]
            await update_user_data(user_id, user_data, db)
            user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
            await telegram_manager.remove_session(user_hash)
            return {"success": True, "message": f"Аккаунт {phone} удалён"}
        else:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
    except Exception as e:
        logging.error(f"Ошибка delete_account: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Get templates
@app.get("/api/templates")
async def get_templates(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        templates = user_data.get('templates', {})
        return {
            "success": True,
            "templates": [
                {
                    "name": name,
                    "text": info.get('text', ''),
                    "media_type": info.get('media_type'),
                    "created_at": info.get('created_at', '')
                }
                for name, info in templates.items()
            ],
            "total": len(templates)
        }
    except Exception as e:
        logging.error(f"Ошибка get_templates: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Create template
@app.post("/api/templates/create")
async def create_template(request: TemplateCreate, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        if 'templates' not in user_data:
            user_data['templates'] = {}
        if request.name in user_data['templates']:
            raise HTTPException(status_code=400, detail="Шаблон с таким именем уже существует")
        user_data['templates'][request.name] = {
            'text': request.text,
            'media_type': request.media_type,
            'file_path': request.file_path,
            'created_at': datetime.now().strftime('%d.%m.%Y %H:%M')
        }
        await update_user_data(user_id, user_data, db)
        return {"success": True, "message": f"Шаблон '{request.name}' создан"}
    except Exception as e:
        logging.error(f"Ошибка create_template: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Delete template
@app.delete("/api/templates/{template_name}")
async def delete_template(template_name: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        if template_name in user_data.get('templates', {}):
            del user_data['templates'][template_name]
            await update_user_data(user_id, user_data, db)
            return {"success": True, "message": f"Шаблон '{template_name}' удалён"}
        else:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
    except Exception as e:
        logging.error(f"Ошибка delete_template: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Broadcast
@app.post("/api/broadcast")
async def broadcast_message(request: BroadcastRequest, background_tasks: BackgroundTasks, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
        userbot = telegram_manager.get_session(user_hash)
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        all_dialogs = await userbot.get_dialogs()
        if request.chat_ids:
            dialogs = [d for d in all_dialogs if d['id'] in request.chat_ids]
        else:
            dialogs = all_dialogs
        if not dialogs:
            raise HTTPException(status_code=400, detail="Нет чатов для рассылки")
        schedule_dt = datetime.now() + timedelta(seconds=request.delay_seconds)
        successful, failed = await userbot.broadcast_message(dialogs, request.text, schedule_dt)
        user_data = await get_user_data(user_id, db)
        stats = user_data.get('stats', {'sent': 0, 'success': 0, 'failed': 0})
        stats['sent'] += len(dialogs)
        stats['success'] += successful
        stats['failed'] += failed
        user_data['stats'] = stats
        await update_user_data(user_id, user_data, db)
        return {
            "success": True,
            "total": len(dialogs),
            "successful": successful,
            "failed": failed,
            "schedule_time": schedule_dt.isoformat()
        }
    except Exception as e:
        logging.error(f"Ошибка broadcast: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Save instant settings
@app.post("/api/instant/settings")
async def save_instant_settings(request: InstantSettingsRequest, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
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
        await update_user_data(user_id, user_data, db)
        return {"success": True, "message": "Настройки сохранены"}
    except Exception as e:
        logging.error(f"Ошибка save_instant_settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Get instant settings
@app.get("/api/instant/settings/{phone}")
async def get_instant_settings(phone: str, user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        settings = user_data.get('instant_settings', {}).get(phone, {
            'enabled': False,
            'template_name': None,
            'delay_seconds': 30
        })
        return {"success": True, "settings": settings}
    except Exception as e:
        logging.error(f"Ошибка get_instant_settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Get stats
@app.get("/api/stats")
async def get_stats(user_id: str = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        user_data = await get_user_data(user_id, db)
        return {
            "success": True,
            "accounts": len(user_data.get('accounts', {})),
            "templates": len(user_data.get('templates', {})),
            "sent": user_data.get('stats', {}).get('sent', 0),
            "success": user_data.get('stats', {}).get('success', 0)
        }
    except Exception as e:
        logging.error(f"Ошибка get_stats: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# API status
@app.get("/api/status")
async def api_status():
    return {
        "status": "online",
        "message": "API работает нормально",
        "endpoints": {
            "auth": "/api/auth/*",
            "accounts": "/api/accounts/*",
            "templates": "/api/templates/*",
            "broadcast": "/api/broadcast",
            "docs": "/docs"
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("=" * 80)
    print("🚀 Telegram Manager API Server v2.0")
    print("=" * 80)
    print(f"\n📡 Запуск на 0.0.0.0:{port}")
    print(f"📖 Документация: http://0.0.0.0:{port}/docs")
    print(f"💚 Health Check: http://0.0.0.0:{port}/health")
    print(f"🌐 Веб-панель: http://0.0.0.0:{port}/")
    print("\n" + "=" * 80)
    uvicorn.run(app, host="0.0.0.0", port=port)
