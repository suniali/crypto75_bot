import logging
import requests

from decouple import config 

logger = logging.getLogger(__name__)

# اطلاعات توکن‌ها و آدرس‌های پایه
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN")
BALE_BOT_TOKEN = config("BALE_BOT_TOKEN")

TELEGRAM_BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BALE_BASE_URL = f"https://tapi.bale.ai/bot{BALE_BOT_TOKEN}"


def send_message_with_fallback(chat_id, text, parse_mode="Markdown", reply_markup=None):
    """
    ارسال پیام با قابلیت Fallback به بله و مدیریت دقیق پارامترها و دکمه‌ها.
    """
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode
    }
    
    # تبدیل ایمن reply_markup به دیکشنری قابل فهم برای بله و تلگرام
    markup_dict = None
    if reply_markup:
        if hasattr(reply_markup, 'to_dict'):
            markup_dict = reply_markup.to_dict()
        elif isinstance(reply_markup, dict):
            markup_dict = reply_markup

    if markup_dict:
        payload['reply_markup'] = markup_dict

    # ۱. تلاش برای ارسال از طریق تلگرام
    try:
        response = requests.post(f"{TELEGRAM_BASE_URL}/sendMessage", json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return {"success": True, "platform": "telegram"}
    except Exception as e:
        logger.warning(f"⚠️ ارسال به تلگرام ناموفق بود ({e}). در حال سوییچ به بله...")

    # ۲. تلاش دوم (Fallback) از طریق بله
    try:
        # در بله گاهی Markdown سخت‌گیرانه‌تر است؛ اگر متن خطا داد می‌توانید parse_mode را موقتاً None کنید
        response = requests.post(f"{BALE_BASE_URL}/sendMessage", json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get("ok"):
            logger.info(f"✅ پیام با موفقیت از طریق بله ارسال شد.")
            return {"success": True, "platform": "bale"}
        else:
            logger.error(f"❌ خطای پلتفرم بله: {data.get('description')}")
            
    except requests.exceptions.HTTPError as he:
        # مدیریت خطاهای خاص مثل پیدا نشدن چت آیدی در بله
        if he.response.status_code == 400:
            logger.error(f"❌ خطای 400 در بله (احتمالاً کاربر ربات بله را استارت نکرده یا چت آیدی نامعتبر است): {he.response.text}")
        else:
            logger.error(f"❌ خطای HTTP در بله: {he}")
    except Exception as e:
        logger.error(f"❌ خطای ناشناخته در ارسال به بله: {e}")

    return {"success": False, "platform": None}