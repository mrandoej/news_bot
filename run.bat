@echo off
echo Запуск бота новостей Саратова...
echo.

REM Проверяем наличие Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Ошибка: Python не найден. Установите Python 3.7+
    pause
    exit /b 1
)

REM Проверяем наличие .env файла
if not exist .env (
    echo Ошибка: Файл .env не найден
    echo Скопируйте .env.example в .env и настройте параметры
    pause
    exit /b 1
)

REM Создаем необходимые директории
if not exist data mkdir data
if not exist logs mkdir logs

REM Устанавливаем зависимости если нужно
if not exist venv (
    echo Создание виртуального окружения...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

REM Запускаем бота
echo Запуск бота...
python main.py run

pause