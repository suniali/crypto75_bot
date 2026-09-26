import pytest
from bot_app.models import TelegramUser
from bot_app.services.user_service import (
    get_or_create_user,
    set_users_blocked_status,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestUserService:

    async def test_get_or_create_user_creates_new_user(self):
        """تست ثبت کاربر جدید در دیتابیس"""
        chat_id = 111222333
        username = "johndoe"
        first_name = "John"

        user = await get_or_create_user(chat_id=chat_id, username=username, first_name=first_name)

        assert user is not None
        assert user.chat_id == chat_id
        assert user.username == username
        assert user.first_name == first_name

        # بررسی وجود کاربر در دیتابیس
        db_user = await TelegramUser.objects.aget(chat_id=chat_id)
        assert db_user.username == username

    async def test_get_or_create_user_updates_existing_user(self):
        """تست به‌روزرسانی اطلاعات کاربر موجود در صورت تغییر username یا first_name"""
        chat_id = 444555666
        # ساخت کاربر اولیه
        initial_user = await TelegramUser.objects.acreate(
            chat_id=chat_id, username="old_user", first_name="OldName"
        )

        # فراخوانی با اطلاعات جدید
        updated_user = await get_or_create_user(
            chat_id=chat_id, username="new_user", first_name="NewName"
        )

        assert updated_user.id == initial_user.id
        assert updated_user.username == "new_user"
        assert updated_user.first_name == "NewName"

        # بررسی در دیتابیس
        await initial_user.arefresh_from_db()
        assert initial_user.username == "new_user"
        assert initial_user.first_name == "NewName"

    async def test_get_or_create_user_no_update_if_unchanged(self):
        """تست عدم تغییر کاربر در صورت یکسان بودن داده‌ها"""
        chat_id = 777888999
        await TelegramUser.objects.acreate(
            chat_id=chat_id, username="same_user", first_name="SameName"
        )

        user = await get_or_create_user(
            chat_id=chat_id, username="same_user", first_name="SameName"
        )

        assert user.username == "same_user"
        assert user.first_name == "SameName"

    async def test_set_users_blocked_status(self):
        """تست بلاک و آن‌بلاک کردن کاربر در دیتابیس"""
        chat_id = 999000111
        user = await TelegramUser.objects.acreate(
            chat_id=chat_id, username="block_test", is_blocked=False
        )

        # بلاک کردن کاربر
        await set_users_blocked_status(user_id=chat_id, is_blocked=True)
        await user.arefresh_from_db()
        assert user.is_blocked is True

        # آن‌بلاک کردن کاربر
        await set_users_blocked_status(user_id=chat_id, is_blocked=False)
        await user.arefresh_from_db()
        assert user.is_blocked is False