@echo off
echo Starting Django Server and Telegram Bot in Windows Terminal...

:: فعال‌سازی محیط مجازی
call .venv\Scripts\activate

:: باز کردن سرور جنگو و ربات در تب‌های جداگانه در ویندوز ترمینال
wt -d . cmd /k ".venv\Scripts\activate && python manage.py runserver" ; split-pane -d . cmd /k ".venv\Scripts\activate && python ./bot_app/bot.py"