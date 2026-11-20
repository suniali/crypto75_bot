import os
import shutil
import subprocess
import logging

from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from datetime import datetime

logger = logging.getLogger(__name__)

AGE_EXT = ".age"


def _encrypt_with_age(source_file: Path, encrypted_file: Path) -> None:
    age_bin = shutil.which("age")
    recipient = os.getenv("BACKUP_AGE_RECIPIENT", "").strip()
    if not age_bin:
        raise RuntimeError("ابزار age روی سیستم نصب نیست.")
    if not recipient:
        raise RuntimeError("متغیر محیطی BACKUP_AGE_RECIPIENT تنظیم نشده است.")

    subprocess.run(
        [age_bin, "-r", recipient, "-o", str(encrypted_file), str(source_file)],
        check=True,
    )


def _decrypt_with_age(encrypted_file: Path, output_file: Path) -> None:
    age_bin = shutil.which("age")
    identity_file = os.getenv("BACKUP_AGE_IDENTITY_FILE", "").strip()
    if not age_bin:
        raise RuntimeError("ابزار age روی سیستم نصب نیست.")
    if not identity_file or not Path(identity_file).exists():
        raise RuntimeError("فایل کلید خصوصی BACKUP_AGE_IDENTITY_FILE یافت نشد.")

    subprocess.run(
        [age_bin, "-d", "-i", identity_file, "-o", str(output_file), str(encrypted_file)],
        check=True,
    )

def get_backups():
    BACKUP_DIR = Path(settings.BASE_DIR) / "backups"
    if not BACKUP_DIR.exists():
        return JsonResponse({"status": "success", "backups": []})

    # لیست فایل‌های رمزنگاری‌شده (و در صورت وجود بکاپ‌های قدیمی‌تر)
    encrypted_backups = sorted(
        BACKUP_DIR.glob(f"db_backup_*.sqlite3{AGE_EXT}"),
        key=os.path.getmtime,
        reverse=True,
    )
    backup_list = [b.name for b in encrypted_backups]
    return JsonResponse({"status": "success", "backups": backup_list})


def backup_database():
    DB_FILE = Path(settings.BASE_DIR) / "db.sqlite3"
    BACKUP_DIR = Path(settings.BASE_DIR) / "backups"

    if not DB_FILE.exists():
        logger.error("Database file not found for backup.")
        return JsonResponse({"status": "error", "message": "فایل دیتابیس پیدا نشد!"}, status=404)

    temp_plain = None
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base_name = f"db_backup_{timestamp}.sqlite3"
        temp_plain = BACKUP_DIR / f".tmp_{base_name}"
        encrypted_path = BACKUP_DIR / f"{base_name}{AGE_EXT}"

        # ساخت نسخه موقت و رمزنگاری آن؛ سپس حذف نسخه موقت خام
        shutil.copy2(DB_FILE, temp_plain)
        _encrypt_with_age(temp_plain, encrypted_path)
        temp_plain.unlink(missing_ok=True)

        # نگهداری فقط ۱۰ بک‌آپ رمزنگاری‌شده آخر
        backups = sorted(
            BACKUP_DIR.glob(f"db_backup_*.sqlite3{AGE_EXT}"),
            key=os.path.getmtime,
        )
        if len(backups) > 10:
            for old_backup in backups[:-10]:
                old_backup.unlink(missing_ok=True)

        logger.info(f"Encrypted database backup created: {encrypted_path.name}")
        return JsonResponse({
            "status": "success",
            "message": f"بک‌آپ رمزنگاری‌شده ذخیره شد: {encrypted_path.name}",
        })
    except Exception as e:
        if temp_plain:
            temp_plain.unlink(missing_ok=True)
        logger.error(f"Error occurred while creating encrypted database backup: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


def restore_backup(backup_filename):
    temp_restored = None
    try:
        DB_FILE = Path(settings.BASE_DIR) / "db.sqlite3"
        BACKUP_DIR = Path(settings.BASE_DIR) / "backups"
        # جلوگیری از Path Traversal با Path(...).name
        safe_name = Path(backup_filename).name
        target_backup = BACKUP_DIR / safe_name

        if not target_backup.exists():
            logger.error(f"Backup file not found: {safe_name}")
            return JsonResponse({"status": "error", "message": "فایل بک‌آپ مورد نظر پیدا نشد"}, status=404)

        # بکاپ اضطراری قبل از بازیابی
        emergency_backup = BACKUP_DIR / "emergency_before_restore.sqlite3"
        if DB_FILE.exists():
            shutil.copy2(DB_FILE, emergency_backup)
            logger.info("Emergency backup created.")

        if safe_name.endswith(AGE_EXT):
            temp_restored = BACKUP_DIR / f".restore_{datetime.now().timestamp()}.sqlite3"
            _decrypt_with_age(target_backup, temp_restored)
            shutil.move(str(temp_restored), str(DB_FILE))
        else:
            shutil.copy2(target_backup, DB_FILE)

        logger.info(f"Database restored from backup: {safe_name}")
        return JsonResponse({
            "status": "success",
            "message": f"دیتابیس با موفقیت از روی فایل {safe_name} بازیابی شد!",
        })
    except Exception as e:
        if temp_restored:
            temp_restored.unlink(missing_ok=True)
        logger.error(f"Error occurred while restoring database from backup: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)