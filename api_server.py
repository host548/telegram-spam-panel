"""
FastAPI Server
API сервер для приёма запросов от веб-панели
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import logging
import json
import os

from telegram_core import TelegramCoreManager

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

app = FastAPI(title="Telegram Scheduler API", version="1.0")

# CORS для работы с фронтендом
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене указать конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Глобальный менеджер сессий
telegram_manager = TelegramCoreManager()

# Хранилище настроек пользователей
SETTINGS_FILE = "user_settings.json"


# ===== МОДЕЛИ ДАННЫХ =====

class AuthStartRequest(BaseModel):
    user_id: int
    phone: str


class AuthCodeRequest(BaseModel):
    user_id: int
    code: str
    phone_code_hash: str


class AuthPasswordRequest(BaseModel):
    user_id: int
    password: str


class BroadcastRequest(BaseModel):
    user_id: int
    text: str
    delay_seconds: int  # Через сколько секунд отправить
    chat_ids: Optional[List[int]] = None  # Если None - всем


class TestMessageRequest(BaseModel):
    user_id: int
    chat_id: int
    text: str
    delay_seconds: int


class TemplateCreate(BaseModel):
    user_id: int
    name: str
    text: str
    media_type: Optional[str] = None


class TemplateSetDefault(BaseModel):
    user_id: int
    template_name: str


# ===== УТИЛИТЫ ДЛЯ НАСТРОЕК =====

def load_settings():
    """Загрузка настроек из файла"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f"Ошибка загрузки настроек: {e}")
        return {}


def save_settings(settings: dict):
    """Сохранение настроек в файл"""
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения настроек: {e}")


def get_user_settings(user_id: int):
    """Получить настройки пользователя"""
    settings = load_settings()
    uid = str(user_id)
    
    if uid not in settings:
        settings[uid] = {
            'auto_broadcast': False,
            'templates': {},
            'default_template': None,
            'phone': None
        }
        save_settings(settings)
    
    return settings[uid]


def update_user_settings(user_id: int, data: dict):
    """Обновить настройки пользователя"""
    settings = load_settings()
    uid = str(user_id)
    
    if uid not in settings:
        settings[uid] = {}
    
    settings[uid].update(data)
    save_settings(settings)


# ===== API ENDPOINTS =====

@app.get("/")
async def root():
    """Проверка работы API"""
    return {
        "status": "online",
        "service": "Telegram Scheduler API",
        "version": "1.0"
    }


@app.get("/health")
async def health_check():
    """Health check для мониторинга"""
    return {"status": "healthy"}


# ----- АВТОРИЗАЦИЯ -----

@app.post("/auth/start")
async def auth_start(request: AuthStartRequest):
    """
    Начало авторизации - отправка кода на телефон
    """
    try:
        userbot = await telegram_manager.create_session(
            request.user_id, 
            request.phone
        )
        
        phone_code_hash = await userbot.send_code()
        
        # Сохраняем телефон в настройках
        update_user_settings(request.user_id, {'phone': request.phone})
        
        return {
            "success": True,
            "phone_code_hash": phone_code_hash,
            "message": f"Код отправлен на {request.phone}"
        }
    except Exception as e:
        logging.error(f"Ошибка auth/start: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/code")
async def auth_code(request: AuthCodeRequest):
    """
    Подтверждение кода из Telegram
    """
    try:
        userbot = telegram_manager.get_session(request.user_id)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        result = await userbot.sign_in(request.code, request.phone_code_hash)
        
        if result.get("success"):
            return {
                "success": True,
                "message": "Авторизация успешна"
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
        logging.error(f"Ошибка auth/code: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/password")
async def auth_password(request: AuthPasswordRequest):
    """
    Подтверждение 2FA пароля
    """
    try:
        userbot = telegram_manager.get_session(request.user_id)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        success = await userbot.check_password(request.password)
        
        if success:
            return {
                "success": True,
                "message": "Авторизация успешна"
            }
        else:
            raise HTTPException(status_code=400, detail="Неверный пароль")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка auth/password: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/auth/status/{user_id}")
async def auth_status(user_id: int):
    """
    Проверка статуса авторизации
    """
    userbot = telegram_manager.get_session(user_id)
    
    if not userbot:
        return {
            "authorized": False,
            "message": "Сессия не найдена"
        }
    
    is_valid = await userbot.check_session()
    
    return {
        "authorized": is_valid,
        "message": "Авторизован" if is_valid else "Требуется авторизация"
    }


@app.post("/auth/logout")
async def logout(user_id: int):
    """
    Выход из аккаунта
    """
    try:
        await telegram_manager.remove_session(user_id)
        
        # Очищаем телефон из настроек
        update_user_settings(user_id, {'phone': None})
        
        return {
            "success": True,
            "message": "Вышли из аккаунта"
        }
    except Exception as e:
        logging.error(f"Ошибка logout: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ----- ПОЛУЧЕНИЕ ДАННЫХ -----

@app.get("/dialogs/{user_id}")
async def get_dialogs(user_id: int):
    """
    Получить список всех чатов/групп/каналов
    """
    try:
        userbot = telegram_manager.get_session(user_id)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        dialogs = await userbot.get_dialogs()
        
        # Статистика
        private = [d for d in dialogs if d['type'] == 'private']
        groups = [d for d in dialogs if d['type'] == 'group']
        channels = [d for d in dialogs if d['type'] == 'channel']
        
        return {
            "success": True,
            "dialogs": dialogs,
            "stats": {
                "total": len(dialogs),
                "private": len(private),
                "groups": len(groups),
                "channels": len(channels)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка get_dialogs: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ----- РАССЫЛКА -----

@app.post("/broadcast")
async def broadcast_message(request: BroadcastRequest, background_tasks: BackgroundTasks):
    """
    Массовая рассылка сообщений
    """
    try:
        userbot = telegram_manager.get_session(request.user_id)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        # Получаем диалоги
        all_dialogs = await userbot.get_dialogs()
        
        # Фильтруем если указаны конкретные чаты
        if request.chat_ids:
            dialogs = [d for d in all_dialogs if d['id'] in request.chat_ids]
        else:
            dialogs = all_dialogs
        
        if not dialogs:
            raise HTTPException(status_code=400, detail="Нет чатов для рассылки")
        
        # Вычисляем время отправки
        schedule_dt = datetime.now() + timedelta(seconds=request.delay_seconds)
        
        # Запускаем рассылку
        successful, failed = await userbot.broadcast_message(
            dialogs, 
            request.text, 
            schedule_dt
        )
        
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


@app.post("/message/test")
async def send_test_message(request: TestMessageRequest):
    """
    Отправка тестового сообщения в один чат
    """
    try:
        userbot = telegram_manager.get_session(request.user_id)
        
        if not userbot:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        
        schedule_dt = datetime.now() + timedelta(seconds=request.delay_seconds)
        
        success = await userbot.schedule_message(
            request.chat_id,
            request.text,
            schedule_dt
        )
        
        if success:
            return {
                "success": True,
                "message": "Сообщение запланировано",
                "schedule_time": schedule_dt.isoformat()
            }
        else:
            raise HTTPException(status_code=400, detail="Ошибка отправки")
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка test message: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ----- ШАБЛОНЫ -----

@app.get("/templates/{user_id}")
async def get_templates(user_id: int):
    """
    Получить все шаблоны пользователя
    """
    settings = get_user_settings(user_id)
    
    return {
        "templates": settings.get('templates', {}),
        "default_template": settings.get('default_template')
    }


@app.post("/templates/create")
async def create_template(request: TemplateCreate):
    """
    Создать новый шаблон
    """
    settings = get_user_settings(request.user_id)
    
    if request.name in settings.get('templates', {}):
        raise HTTPException(status_code=400, detail="Шаблон уже существует")
    
    template_data = {
        'text': request.text,
        'media_type': request.media_type,
        'created_at': datetime.now().strftime('%d.%m.%Y %H:%M')
    }
    
    if 'templates' not in settings:
        settings['templates'] = {}
    
    settings['templates'][request.name] = template_data
    
    # Первый шаблон = по умолчанию
    if len(settings['templates']) == 1:
        settings['default_template'] = request.name
    
    update_user_settings(request.user_id, settings)
    
    return {
        "success": True,
        "message": f"Шаблон '{request.name}' создан"
    }


@app.post("/templates/set-default")
async def set_default_template(request: TemplateSetDefault):
    """
    Установить шаблон по умолчанию
    """
    settings = get_user_settings(request.user_id)
    
    if request.template_name not in settings.get('templates', {}):
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    
    update_user_settings(request.user_id, {
        'default_template': request.template_name
    })
    
    return {
        "success": True,
        "message": f"Шаблон '{request.template_name}' установлен по умолчанию"
    }


@app.delete("/templates/{user_id}/{template_name}")
async def delete_template(user_id: int, template_name: str):
    """
    Удалить шаблон
    """
    settings = get_user_settings(user_id)
    
    if template_name not in settings.get('templates', {}):
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    
    del settings['templates'][template_name]
    
    # Сбросить default если удаляем его
    if settings.get('default_template') == template_name:
        settings['default_template'] = None
    
    update_user_settings(user_id, settings)
    
    return {
        "success": True,
        "message": f"Шаблон '{template_name}' удалён"
    }


# ----- НАСТРОЙКИ -----

@app.get("/settings/{user_id}")
async def get_settings(user_id: int):
    """
    Получить настройки пользователя
    """
    return get_user_settings(user_id)


@app.post("/settings/{user_id}/auto-broadcast")
async def toggle_auto_broadcast(user_id: int, enabled: bool):
    """
    Включить/выключить авто-рассылку
    """
    settings = get_user_settings(user_id)
    
    if enabled and not settings.get('default_template'):
        raise HTTPException(
            status_code=400, 
            detail="Нужен шаблон по умолчанию"
        )
    
    update_user_settings(user_id, {'auto_broadcast': enabled})
    
    return {
        "success": True,
        "auto_broadcast": enabled,
        "message": "Авто-рассылка " + ("включена" if enabled else "выключена")
    }


# ----- ОЧИСТКА -----

@app.post("/cleanup/{user_id}")
async def cleanup_user_sessions(user_id: int):
    """
    Очистка всех сессий пользователя
    """
    try:
        await telegram_manager.remove_session(user_id)
        
        return {
            "success": True,
            "message": "Сессии очищены"
        }
    except Exception as e:
        logging.error(f"Ошибка cleanup: {e}")
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    import os
    
    # Получаем порт из переменной окружения (Render использует PORT)
    port = int(os.environ.get("PORT", 8000))
    
    print("=" * 70)
    print("🚀 Telegram Scheduler API Server")
    print("=" * 70)
    print(f"\n📡 Запуск сервера на 0.0.0.0:{port}")
    print(f"📖 Документация: http://0.0.0.0:{port}/docs")
    print("\n" + "=" * 70)
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=port,
        log_level="info"
    )
