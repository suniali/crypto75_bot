@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo [INFO] Pulling latest changes from Git...
git pull origin master
echo [INFO] Git pull completed successfully.
pause

if errorlevel 1 (
    echo [ERROR] Git pull failed! Check for merge conflicts or uncommitted changes.
    pause
    exit /b 1
)
exit /b %errorlevel%