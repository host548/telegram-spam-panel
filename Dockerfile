FROM python:3.12-slim

# Установка системных зависимостей для cryptography (OpenSSL) и других
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app
COPY . /app

# Установка Python-зависимостей
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Запуск сервера (PORT из env Render)
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "$PORT"]
