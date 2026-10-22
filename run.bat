@echo off
echo Starting Django Server and Telegram Bot in Windows Terminal...

:: ایجاد محیط مجازی پایتون
call python -m venv .venv

:: فعال‌سازی محیط مجازی
call .venv\Scripts\activate

:: ارتقاء pip به آخرین نسخه
call python -m pip install --upgrade pip

:: نصب وابستگی‌ها از فایل requirements.txt
call pip install -r requirements.txt

:: اجرای مهاجرت‌های پایگاه داده
call python manage.py migrate

:: باز کردن سرور جنگو و ربات در تب‌های جداگانه در ویندوز ترمینال
wt -d . cmd /k ".venv\Scripts\activate && python run_app.py" ; split-pane -d . cmd /k ".venv\Scripts\activate && python ./bot_app/bot.py"