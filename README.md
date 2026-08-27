# 📈 Crypto Trading Alert Bot (Django + Telegram API)

یک سیستم هوشمند و ماژولار برای ثبت هشدارهای قیمت بازار کریپتوکارنسی با قابلیت اتصال مستقیم ربات تلگرام به دیتابیس جانگو و سرویس پایش لحظه‌ای قیمت‌ها.

## 🚀 قابلیت‌های کلیدی

- **ثبت سریع هشدارها:** ثبت سفارش/هشدار قیمت تنها با ارسال یک دستور ساده به تلگرام (`/alert BTCUSDT 65000`).
- **پنل ادمین قدرتمند:** مشاهده، فیلتر و مدیریت تمام هشدارهای کاربران از طریق Django Admin.
- **پایش لحظه‌ای (Real-time Price Checker):** چک کردن خودکار قیمت‌ها از API صرافی با معماری Async و ارسال سریع پیام به تلگرام در صورت رسیدن به قیمت هدف.
- **معماری امن:** مدیریت کلیدهای حساس و توکن‌ها با استفاده از `python-decouple` و فایل `.env`.

## 🛠 فناوری‌های استفاده‌شده

- **Backend:** Python 3.12, Django 5.0
- **Bot Engine:** python-telegram-bot (Async)
- **Database:** SQLite / PostgreSQL
- **API Integration:** Requests, REST API
- **Security:** Python-Decouple

## ⚙️ نحوه اجرا و راه‌اندازی

1. مخزن را کلون کنید :
   ```bash
   git clone https://github.com/suniali/crypto75_bot.git
   cd crypto75_bot

2. محیط مجازی را بسازید و کتابخانه‌ها را نصب کنید :
   ```bash
   python -m venv venv
   source venv/bin/activate  # یا venv\Scripts\activate در ویندوز
   pip install -r requirements.txt

3. فایل .env را در کنار manage.py بسازید و مقادیر زیر را قرار دهید :
    ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   SECRET_KEY=your_django_secret_key
   DEBUG=True

4. مایگریشن‌ها را اجرا کنید : 
   ```bash
   python manage.py migrate

5. اجرا کردن ربات و موتور پایش :
   ```bash
   # ترمینال ۱: اجرای ربات
   python bot_app/bot.py

   # ترمینال ۲: اجرای موتور بررسی قیمت
   python bot_app/checker.py