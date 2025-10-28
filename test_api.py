"""
Скрипт для тестирования API перед деплоем
Запуск: python test_api.py
"""

import requests
import time

# Замените на ваш URL (локальный или на Render)
API_URL = "http://localhost:8000"
# API_URL = "https://telegram-scheduler-api.onrender.com"

USER_ID = 123456  # Тестовый ID
PHONE = "+79991234567"  # Ваш номер для теста


def test_health():
    """Проверка health endpoint"""
    print("\n🔍 Проверка health...")
    try:
        response = requests.get(f"{API_URL}/health")
        if response.status_code == 200:
            print("✅ Health check: OK")
            print(f"   Ответ: {response.json()}")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False


def test_root():
    """Проверка root endpoint"""
    print("\n🔍 Проверка root endpoint...")
    try:
        response = requests.get(f"{API_URL}/")
        if response.status_code == 200:
            print("✅ Root endpoint: OK")
            data = response.json()
            print(f"   Service: {data.get('service')}")
            print(f"   Version: {data.get('version')}")
            return True
        else:
            print(f"❌ Root endpoint failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


def test_docs():
    """Проверка Swagger документации"""
    print("\n🔍 Проверка документации...")
    try:
        response = requests.get(f"{API_URL}/docs")
        if response.status_code == 200:
            print("✅ Swagger docs: OK")
            print(f"   URL: {API_URL}/docs")
            return True
        else:
            print(f"❌ Docs failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


def test_auth_status():
    """Проверка статуса авторизации"""
    print("\n🔍 Проверка auth/status...")
    try:
        response = requests.get(f"{API_URL}/auth/status/{USER_ID}")
        if response.status_code == 200:
            data = response.json()
            print("✅ Auth status: OK")
            print(f"   Authorized: {data.get('authorized')}")
            print(f"   Message: {data.get('message')}")
            return True
        else:
            print(f"❌ Auth status failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


def test_auth_start():
    """Тест начала авторизации (требует реальный номер)"""
    print("\n⚠️  Тест auth/start пропущен (требует реальный номер)")
    print(f"   Чтобы протестировать, запустите вручную:")
    print(f"   POST {API_URL}/auth/start")
    print(f"   Body: {{'user_id': {USER_ID}, 'phone': '{PHONE}'}}")
    return None


def run_all_tests():
    """Запустить все тесты"""
    print("=" * 70)
    print("🚀 Тестирование Telegram Scheduler API")
    print("=" * 70)
    print(f"📡 API URL: {API_URL}")
    print(f"👤 Test User ID: {USER_ID}")
    
    results = {
        "Health Check": test_health(),
        "Root Endpoint": test_root(),
        "Swagger Docs": test_docs(),
        "Auth Status": test_auth_status(),
        "Auth Start": test_auth_start(),
    }
    
    print("\n" + "=" * 70)
    print("📊 РЕЗУЛЬТАТЫ:")
    print("=" * 70)
    
    passed = sum(1 for v in results.values() if v is True)
    failed = sum(1 for v in results.values() if v is False)
    skipped = sum(1 for v in results.values() if v is None)
    
    for test_name, result in results.items():
        if result is True:
            status = "✅ PASS"
        elif result is False:
            status = "❌ FAIL"
        else:
            status = "⏭️  SKIP"
        print(f"{status} - {test_name}")
    
    print("\n" + "=" * 70)
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"⏭️  Skipped: {skipped}")
    print("=" * 70)
    
    if failed == 0:
        print("\n🎉 Все тесты пройдены! Готово к деплою!")
    else:
        print(f"\n⚠️  {failed} тест(ов) провалено. Исправьте перед деплоем.")
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)