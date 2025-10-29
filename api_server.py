"""
FastAPI Server для Telegram Manager
API сервер для работы с веб-панелью и Supabase
Готов к деплою на Render
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import json
import os
from pathlib import Path

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

# CORS - разрешаем запросы от Supabase и фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://wdmyvtyvalcczvittgci.supabase.co",
        "http://localhost:3000",
        "http://localhost:8000",
        "*"  # В продакшене заменить на конкретные домены
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Глобальный менеджер Telegram сессий
telegram_manager = TelegramCoreManager()

# Файлы для хранения данных
SETTINGS_FILE = "user_settings.json"
SESSIONS_FILE = "sessions_data.json"

# ===== МОДЕЛИ ДАННЫХ =====

class AuthStartRequest(BaseModel):
    user_id: str  # Изменено на str для совместимости с Supabase UUID
    phone: str


class AuthCodeRequest(BaseModel):
    user_id: str
    code: str
    phone_code_hash: str


class AuthPasswordRequest(BaseModel):
    user_id: str
    password: str


class AccountInfo(BaseModel):
    user_id: str
    phone: str
    status: str = "active"


class BroadcastRequest(BaseModel):
    user_id: str
    account_phone: str
    text: str
    delay_seconds: int = 30
    chat_ids: Optional[List[int]] = None
    file_path: Optional[str] = None


class TemplateCreate(BaseModel):
    user_id: str
    name: str
    text: str
    media_type: Optional[str] = None
    file_path: Optional[str] = None


class InstantSettingsRequest(BaseModel):
    user_id: str
    account_phone: str
    enabled: bool
    template_name: Optional[str] = None
    delay_seconds: int = 30


# ===== УТИЛИТЫ ДЛЯ ДАННЫХ =====

def load_json_file(filename: str) -> dict:
    """Универсальная загрузка JSON файла"""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Ошибка загрузки {filename}: {e}")
        return {}


def save_json_file(filename: str, data: dict):
    """Универсальное сохранение в JSON файл"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения {filename}: {e}")


def get_user_data(user_id: str) -> dict:
    """Получить данные пользователя"""
    settings = load_json_file(SETTINGS_FILE)
    
    if user_id not in settings:
        settings[user_id] = {
            'accounts': {},  # phone -> {status, auth_date}
            'templates': {},
            'instant_settings': {},  # phone -> {enabled, template, delay}
            'stats': {
                'sent': 0,
                'success': 0,
                'failed': 0
            }
        }
        save_json_file(SETTINGS_FILE, settings)
    
    return settings[user_id]


def update_user_data(user_id: str, data: dict):
    """Обновить данные пользователя"""
    settings = load_json_file(SETTINGS_FILE)
    
    if user_id not in settings:
        settings[user_id] = {}
    
    settings[user_id].update(data)
    save_json_file(SETTINGS_FILE, settings)


def save_session_info(user_id: str, phone: str, phone_code_hash: str):
    """Сохранить информацию о сессии"""
    sessions = load_json_file(SESSIONS_FILE)
    sessions[f"{user_id}:{phone}"] = {
        'phone_code_hash': phone_code_hash,
        'timestamp': datetime.now().isoformat()
    }
    save_json_file(SESSIONS_FILE, sessions)


def get_session_info(user_id: str, phone: str) -> dict:
    """Получить информацию о сессии"""
    sessions = load_json_file(SESSIONS_FILE)
    return sessions.get(f"{user_id}:{phone}", {})


# ===== ГЛАВНАЯ СТРАНИЦА =====

@app.get("/", response_class=HTMLResponse)
async def serve_web_panel():
    """Обслуживание веб-панели"""
    try:
        panel_path = Path("web_panel.html")
        if panel_path.exists():
            return FileResponse(panel_path)
        else:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Telegram Manager</title>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            display: flex;
                            justify-content: center;
                            align-items: center;
                            height: 100vh;
                            margin: 0;
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            color: white;
                        }
                        .container {
                            text-align: center;
                            padding: 40px;
                            background: rgba(255,255,255,0.1);
                            border-radius: 20px;
                            backdrop-filter: blur(10px);
                        }
                        h1 { margin: 0 0 20px 0; }
                        a { color: #fff; text-decoration: underline; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>🚀 Telegram Manager API</h1>
                        <p>API сервер успешно запущен!</p>
                        <p>📖 Документация: <a href="/docs">/docs</a></p>
                        <p>💚 Статус: <a href="/health">/health</a></p>
                    </div>
                </body>
                </html>
                """,
                status_code=200
            )
    except Exception as e:
        logging.error(f"Ошибка загрузки панели: {e}")
        raise HTTPException(status_code=500, detail="Ошибка загрузки панели")


@app.get("/health")
async def health_check():
    """Health check для Render"""
    return {
        "status": "healthy",
        "service": "Telegram Manager API",
        "version": "2.0",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/status")
async def api_status():
    """Проверка работы API"""
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


# ===== АВТОРИЗАЦИЯ TELEGRAM =====

@app.post("/api/auth/start")
async def start_auth(request: AuthStartRequest):
    """Шаг 1: Отправка кода авторизации"""
    try:
        # Создаём сессию для пользователя
        userbot = await telegram_manager.create_session(
            int(request.user_id) if request.user_id.isdigit() else hash(request.user_id),
            request.phone
        )
        
        # Отправляем код
        phone_code_hash = await userbot.send_code()
        
        # Сохраняем информацию о сессии
        save_session_info(request.user_id, request.phone, phone_code_hash)
        
        return {
            "success": True,
            "phone_code_hash": phone_code_hash,
            "message": f"Код отправлен на {request.phone}"
        }
    except Exception as e:
        logging.error(f"Ошибка auth/start: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка отправки кода: {str(e)}")


@app.post("/api/auth/verify-code")
async def verify_code(request: AuthCodeRequest):
    """Шаг 2: Проверка кода из Telegram"""
    try:
        user_hash = int(request.user_id) if request.user_id.isdigit() else hash(request.user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена. Начните авторизацию заново")
        
        result = await userbot.sign_in(request.code, request.phone_code_hash)
        
        if result.get("success"):
            # Сохраняем аккаунт в данных пользователя
            user_data = get_user_data(request.user_id)
            if 'accounts' not in user_data:
                user_data['accounts'] = {}
            
            # Получаем информацию об аккаунте
            me = await userbot.client.get_me()
            
            user_data['accounts'][me.phone] = {
                'status': 'active',
                'username': me.username or 'Нет username',
                'first_name': me.first_name or '',
                'auth_date': datetime.now().isoformat()
            }
            
            update_user_data(request.user_id, user_data)
            
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
            return {
                "success": False,
                "needs_password": True,
                "message": "Требуется 2FA пароль"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Неверный код")
            )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка verify-code: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/verify-password")
async def verify_password(request: AuthPasswordRequest):
    """Шаг 3: Проверка 2FA пароля"""
    try:
        user_hash = int(request.user_id) if request.user_id.isdigit() else hash(request.user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        success = await userbot.check_password(request.password)
        
        if success:
            # Получаем информацию об аккаунте
            me = await userbot.client.get_me()
            
            # Сохраняем аккаунт
            user_data = get_user_data(request.user_id)
            if 'accounts' not in user_data:
                user_data['accounts'] = {}
            
            user_data['accounts'][me.phone] = {
                'status': 'active',
                'username': me.username or 'Нет username',
                'first_name': me.first_name or '',
                'auth_date': datetime.now().isoformat()
            }
            
            update_user_data(request.user_id, user_data)
            
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
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка verify-password: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ===== УПРАВЛЕНИЕ АККАУНТАМИ =====

@app.get("/api/accounts/{user_id}")
async def get_accounts(user_id: str):
    """Получить все аккаунты пользователя"""
    try:
        user_data = get_user_data(user_id)
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


@app.delete("/api/accounts/{user_id}/{phone}")
async def delete_account(user_id: str, phone: str):
    """Удалить аккаунт"""
    try:
        user_data = get_user_data(user_id)
        
        if phone in user_data.get('accounts', {}):
            del user_data['accounts'][phone]
            update_user_data(user_id, user_data)
            
            # Удаляем сессию
            user_hash = int(user_id) if user_id.isdigit() else hash(user_id)
            await telegram_manager.remove_session(user_hash)
            
            return {
                "success": True,
                "message": f"Аккаунт {phone} удалён"
            }
        else:
            raise HTTPException(status_code=404, detail="Аккаунт не найден")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка delete_account: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ===== ШАБЛОНЫ =====

@app.get("/api/templates/{user_id}")
async def get_templates(user_id: str):
    """Получить все шаблоны"""
    try:
        user_data = get_user_data(user_id)
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


@app.post("/api/templates/create")
async def create_template(request: TemplateCreate):
    """Создать новый шаблон"""
    try:
        user_data = get_user_data(request.user_id)
        
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
        
        update_user_data(request.user_id, user_data)
        
        return {
            "success": True,
            "message": f"Шаблон '{request.name}' создан"
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка create_template: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/templates/{user_id}/{template_name}")
async def delete_template(user_id: str, template_name: str):
    """Удалить шаблон"""
    try:
        user_data = get_user_data(user_id)
        
        if template_name in user_data.get('templates', {}):
            del user_data['templates'][template_name]
            update_user_data(user_id, user_data)
            
            return {
                "success": True,
                "message": f"Шаблон '{template_name}' удалён"
            }
        else:
            raise HTTPException(status_code=404, detail="Шаблон не найден")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка delete_template: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ===== РАССЫЛКА =====

@app.post("/api/broadcast")
async def broadcast_message(request: BroadcastRequest, background_tasks: BackgroundTasks):
    """Массовая рассылка"""
    try:
        user_hash = int(request.user_id) if request.user_id.isdigit() else hash(request.user_id)
        userbot = telegram_manager.get_session(user_hash)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        # Получаем диалоги
        all_dialogs = await userbot.get_dialogs()
        
        # Фильтруем
        if request.chat_ids:
            dialogs = [d for d in all_dialogs if d['id'] in request.chat_ids]
        else:
            dialogs = all_dialogs
        
        if not dialogs:
            raise HTTPException(status_code=400, detail="Нет чатов для рассылки")
        
        # Планируем рассылку
        schedule_dt = datetime.now() + timedelta(seconds=request.delay_seconds)
        
        successful, failed = await userbot.broadcast_message(
            dialogs,
            request.text,
            schedule_dt
        )
        
        # Обновляем статистику
        user_data = get_user_data(request.user_id)
        stats = user_data.get('stats', {'sent': 0, 'success': 0, 'failed': 0})
        stats['sent'] += len(dialogs)
        stats['success'] += successful
        stats['failed'] += failed
        user_data['stats'] = stats
        update_user_data(request.user_id, user_data)
        
        return {
            "success": True,
            "total": len(dialogs),
            "successful": successful,
            "failed": failed,
            "schedule_time": schedule_dt.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка broadcast: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ===== МОМЕНТАЛЬНАЯ ОТПРАВКА =====

@app.post("/api/instant/settings")
async def save_instant_settings(request: InstantSettingsRequest):
    """Сохранить настройки моментальной отправки"""
    try:
        user_data = get_user_data(request.user_id)
        
        if 'instant_settings' not in user_data:
            user_data['instant_settings'] = {}
        
        user_data['instant_settings'][request.account_phone] = {
            'enabled': request.enabled,
            'template_name': request.template_name,
            'delay_seconds': request.delay_seconds,
            'updated_at': datetime.now().isoformat()
        }
        
        update_user_data(request.user_id, user_data)
        
        return {
            "success": True,
            "message": "Настройки сохранены"
        }
    except Exception as e:
        logging.error(f"Ошибка save_instant_settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/instant/settings/{user_id}/{phone}")
async def get_instant_settings(user_id: str, phone: str):
    """Получить настройки моментальной отправки"""
    try:
        user_data = get_user_data(user_id)
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
        logging.error(f"Ошибка get_instant_settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ===== СТАТИСТИКА =====

@app.get("/api/stats/{user_id}")
async def get_stats(user_id: str):
    """Получить статистику пользователя"""
    try:
        user_data = get_user_data(user_id)
        
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


# ===== ЗАПУСК СЕРВЕРА =====

if __name__ == "__main__":
    import uvicorn
    
    # Получаем порт из переменной окружения (для Render)
    port = int(os.environ.get("PORT", 10000))
    
    print("=" * 80)
    print("🚀 Telegram Manager API Server v2.0")
    print("=" * 80)
    print(f"\n📡 Запуск на 0.0.0.0:{port}")
    print(f"📖 Документация: http://0.0.0.0:{port}/docs")
    print(f"💚 Health Check: http://0.0.0.0:{port}/health")
    print(f"🌐 Веб-панель: http://0.0.0.0:{port}/")
    print("\n" + "=" * 80)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
