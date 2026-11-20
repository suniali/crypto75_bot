@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo [INFO] Starting Django Server, Caddy HTTPS Proxy and Telegram Bot environment...

:: ---------------------------------------------------------
:: 1. بررسی و نصب age در صورت نبودن (بار اول)
:: ---------------------------------------------------------
where age >nul 2>&1
if errorlevel 1 (
    echo [INFO] 'age' not found. Attempting installation via winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] winget is not available. Please install 'age' manually from GitHub releases ^(FiloSottile/age^) and add it to PATH.
        goto :error_exit
    )
    winget install --id FiloSottile.age -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [ERROR] Failed to install 'age' via winget.
        goto :error_exit
    )
    set "PATH=%PATH%;%LocalAppData%\Microsoft\WinGet\Packages\FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe"
) else (
    echo [INFO] 'age' is already installed.
)

:: ---------------------------------------------------------
:: 2. بررسی و نصب Caddy در صورت نبودن (بار اول)
:: ---------------------------------------------------------
where caddy >nul 2>&1
if errorlevel 1 (
    echo [INFO] 'Caddy' not found. Attempting installation via winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] winget is not available. Please install 'Caddy' manually and add it to PATH.
        goto :error_exit
    )
    winget install --id CaddyServer.Caddy -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo [ERROR] Failed to install 'Caddy' via winget.
        goto :error_exit
    )
    set "PATH=%PATH%;%LocalAppData%\Microsoft\WinGet\Packages\CaddyServer.Caddy_Microsoft.Winget.Source_8wekyb3d8bbwe"
) else (
    echo [INFO] 'Caddy' is already installed.
)

:: ---------------------------------------------------------
:: 3. تولید خودکار کلید age و آپدیت تمیز .env (بدون BOM)
:: ---------------------------------------------------------
set "AGE_KEY_DIR=%USERPROFILE%\.jarvis-keys"
set "AGE_KEY_FILE=%AGE_KEY_DIR%\backup-key.txt"
set "AGE_PUB_KEY="

if not exist "%AGE_KEY_DIR%" (
    mkdir "%AGE_KEY_DIR%" >nul 2>&1
)

where age-keygen >nul 2>&1
if errorlevel 1 (
    echo [WARN] 'age-keygen' not found in PATH. Skipping automatic key generation.
) else (
    if not exist "%AGE_KEY_FILE%" (
        echo [INFO] Generating new age backup keypair at "%AGE_KEY_FILE%"...
        age-keygen -o "%AGE_KEY_FILE%" >nul 2>&1
        if errorlevel 1 (
            echo [WARN] Failed to generate age keypair automatically.
        ) else (
            echo [OK] New age keypair generated. Keep "%AGE_KEY_FILE%" safe and out of Git!
        )
    ) else (
        echo [INFO] Existing age keypair found at "%AGE_KEY_FILE%".
    )

    if exist "%AGE_KEY_FILE%" (
        for /f "delims=" %%k in ('powershell -NoProfile -Command "$m = Select-String -LiteralPath $env:AGE_KEY_FILE -Pattern 'public key:\s*(age1[a-z0-9]+)'; if ($m) { $m.Matches[0].Groups[1].Value }"') do (
            set "AGE_PUB_KEY=%%k"
        )
    )
)

if defined AGE_PUB_KEY (
    set "BACKUP_AGE_RECIPIENT=%AGE_PUB_KEY%"
)
if exist "%AGE_KEY_FILE%" (
    set "BACKUP_AGE_IDENTITY_FILE=%AGE_KEY_FILE%"
)

if not exist ".env" (
    echo [INFO] '.env' not found. Creating '.env' without BOM...
    powershell -NoProfile -Command ^
        "$lines = @('DASHBOARD_PORT=8000', 'HTTPS_PORT=8443'); " ^
        "if ($env:AGE_PUB_KEY) { $lines += ('BACKUP_AGE_RECIPIENT=' + $env:AGE_PUB_KEY) }; " ^
        "if (Test-Path -LiteralPath $env:AGE_KEY_FILE) { $lines += ('BACKUP_AGE_IDENTITY_FILE=' + $env:AGE_KEY_FILE) }; " ^
        "[System.IO.File]::WriteAllLines((Join-Path (Get-Location) '.env'), [string[]]$lines, [System.Text.UTF8Encoding]::new($false))"
) else (
    if defined AGE_PUB_KEY (
        powershell -NoProfile -Command ^
            "$p = (Resolve-Path -LiteralPath '.env').Path; " ^
            "$lines = [System.IO.File]::ReadAllLines($p, [System.Text.UTF8Encoding]::new($false)); " ^
            "$lines = [string[]]($lines | Where-Object { $_ -notmatch '^BACKUP_AGE_RECIPIENT=' -and $_ -notmatch '^BACKUP_AGE_IDENTITY_FILE=' }); " ^
            "$lines += ('BACKUP_AGE_RECIPIENT=' + $env:AGE_PUB_KEY); " ^
            "$lines += ('BACKUP_AGE_IDENTITY_FILE=' + $env:AGE_KEY_FILE); " ^
            "[System.IO.File]::WriteAllLines($p, $lines, [System.Text.UTF8Encoding]::new($false))"
        echo [OK] Updated BACKUP_AGE_RECIPIENT and BACKUP_AGE_IDENTITY_FILE in .env without BOM.
    )
)

:: ---------------------------------------------------------
:: 4. ساخت Caddyfile در صورت نبودن
:: ---------------------------------------------------------
if not exist Caddyfile (
    echo [INFO] Creating default Caddyfile for local HTTPS...
    (
        echo {
        echo     local_certs
        echo }
        echo.
        echo https://localhost:8443 {
        echo     tls internal
        echo     reverse_proxy 127.0.0.1:8000
        echo }
    ) > Caddyfile
)

:: ---------------------------------------------------------
:: 5. ساخت و Trust کردن گواهی Root لوکال Caddy در بار اول
:: ---------------------------------------------------------
set "CADDY_ROOT_CRT=%APPDATA%\Caddy\pki\authorities\local\root.crt"

if not exist "%CADDY_ROOT_CRT%" (
    echo [INFO] Caddy root certificate not found at "%CADDY_ROOT_CRT%". Generating PKI certificate...
    powershell -NoProfile -Command ^
        "$p = Start-Process -FilePath 'caddy' -ArgumentList 'run --config Caddyfile --adapter caddyfile' -WindowStyle Hidden -PassThru; " ^
        "Start-Sleep -Seconds 3; " ^
        "if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }"
)

if exist "%CADDY_ROOT_CRT%" (
    echo [INFO] Registering Caddy root certificate in Windows Trusted Root store...
    certutil -addstore -f "ROOT" "%CADDY_ROOT_CRT%" >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Failed to add Caddy root certificate automatically ^(Administrator privileges may be required^).
        echo [WARN] Run CMD or this .bat as Administrator once, or run:
        echo        certutil -addstore -f "ROOT" "%CADDY_ROOT_CRT%"
    ) else (
        echo [OK] Caddy local root certificate is trusted in Windows ROOT store.
    )
) else (
    echo [WARN] Caddy root.crt was not created yet; Caddy will generate it when the server pane starts.
)

:: ---------------------------------------------------------
:: 6. محیط مجازی و پکیج‌های پایتون
:: ---------------------------------------------------------
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

:: ---------------------------------------------------------
:: 7. مایگریشن، کالکت استاتیک و بررسی سوپریوزر
:: ---------------------------------------------------------
echo [INFO] Applying database migrations...
python manage.py migrate
if errorlevel 1 (
    echo [ERROR] Database migration failed!
    goto :error_exit
)

echo [INFO] Applying collectstatic...
python manage.py collectstatic --noinput
if errorlevel 1 (
    echo [ERROR] Static files collection failed!
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

:: ---------------------------------------------------------
:: 8. اجرای هم‌زمان Waitress، Caddy و Bot در Windows Terminal
:: ---------------------------------------------------------
echo [INFO] Launching Waitress server, Caddy HTTPS proxy, and Telegram Bot...
echo [INFO] Access your dashboard at: https://localhost:8443
wt -d . cmd /k ".venv\Scripts\activate && python ./bot_app/bot.py" ; split-pane -H --size 0.25 -d . cmd /k ".venv\Scripts\activate && python run_app.py" ; split-pane -V --size 0.45 -d . cmd /k "caddy run --config Caddyfile --adapter caddyfile"
exit /b 0

:error_exit
echo.
echo [!] Program halted due to an error.
pause
exit /b 1