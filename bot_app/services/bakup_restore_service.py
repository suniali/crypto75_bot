import os
import shutil
import logging

from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from datetime import datetime

logger = logging.getLogger(__name__)

def get_backups():
    BACKUP_DIR = Path(settings.BASE_DIR) / "backups"

    if not BACKUP_DIR.exists():
        return JsonResponse({"status": "success", "backups": []})

    # پیدا کردن و مرتب‌سازی فایل‌ها بر اساس تاریخ (جدیدترین اول)
    backups = sorted(BACKUP_DIR.glob("db_backup_*.sqlite3"), key=os.path.getmtime, reverse=True)
    backup_list = [b.name for b in backups]

    return JsonResponse({"status": "success", "backups": backup_list})
    

def backup_database():
    DB_FILE = Path(settings.BASE_DIR) / "db.sqlite3"
    BACKUP_DIR = Path(settings.BASE_DIR) / "backups"

    if not DB_FILE.exists():
        logger.error("Database file not found for backup.")
        return JsonResponse({"status": "error", "message": "فایل دیتابیس پیدا نشد!"}, status=404)

    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"db_backup_{timestamp}.sqlite3"
        backup_path = BACKUP_DIR / backup_filename

        shutil.copy2(DB_FILE, backup_path)

        # نگهداری فقط ۱۰ بک‌آپ آخر برای جلوگیری از پر شدن حافظه
        backups = sorted(BACKUP_DIR.glob("db_backup_*.sqlite3"), key=os.path.getmtime)
        if len(backups) > 10:
            for old_backup in backups[:-10]:
                old_backup.unlink()

        logger.info(f"Database backup created: {backup_filename}")
        return JsonResponse({"status": "success", "message": f"بک‌آپ با موفقیت ذخیره شد: {backup_filename}"})
    except Exception as e:
        logger.error(f"Error occurred while creating database backup: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

def restore_backup(backup_filename):
    try:
        DB_FILE = Path(settings.BASE_DIR) / "db.sqlite3"
        BACKUP_DIR = Path(settings.BASE_DIR) / "backups"
        target_backup = BACKUP_DIR / backup_filename

        if not target_backup.exists():
            logger.error(f"Backup file not found: {backup_filename}")
            return JsonResponse({"status": "error", "message": "فایل بک‌آپ مورد نظر پیدا نشد"}, status=404)

        # قبل از جایگزینی، یک ایمنی موقت از وضعیت فعلی می‌گیریم
        emergency_backup = BACKUP_DIR / "emergency_before_restore.sqlite3"
        if DB_FILE.exists():
            shutil.copy2(DB_FILE, emergency_backup)
            logger.info("Emergency backup created.")

        # جایگزینی فایل بک‌آپ با دیتابیس اصلی
        shutil.copy2(target_backup, DB_FILE)

        logger.info(f"Database restored from backup: {backup_filename}")
        return JsonResponse({"status": "success", "message": f"دیتابیس با موفقیت از روی فایل {backup_filename} بازیابی شد!"})
    except Exception as e:
        logger.error(f"Error occurred while restoring database from backup: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)