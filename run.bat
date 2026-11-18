@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo [INFO] Starting Django Server and Telegram Bot environment...

if not exist .venv (
    echo [INFO] Creating virtual environment ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        goto :error_exit
    )
)

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo [ERROR] Virtual environment not found!
    goto :error_exit
)

python -m pip install --upgrade pip
echo [INFO] Installing requirements...
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    goto :error_exit
)

echo [INFO] Applying database migrations...
python manage.py migrate
if errorlevel 1 (
    echo [ERROR] Database migration failed!
    goto :error_exit
)

echo [INFO] Checking for superuser...
set HAS_SUPERUSER=0
for /f "delims=" %%i in ('python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings'); django.setup(); from django.contrib.auth.models import User; print(1 if User.objects.filter(is_superuser=True).exists() else 0)"') do set HAS_SUPERUSER=%%i

if "%HAS_SUPERUSER%"=="0" (
    echo [NOTICE] No superuser found. Create your admin account:
    python manage.py createsuperuser
    if errorlevel 1 (
        echo [ERROR] Superuser creation failed.
        goto :error_exit
    )
) else (
    echo [INFO] Superuser already exists.
)

echo [INFO] Launching Django server and Bot...
wt -d . cmd /k ".venv\Scripts\activate && python run_app.py" ; split-pane -d . cmd /k ".venv\Scripts\activate && python ./bot_app/bot.py"
exit /b 0

:error_exit
echo.
echo [!] Program halted due to an error.
pause
exit /b 1