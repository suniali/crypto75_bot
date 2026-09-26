# tests/test_start_stop.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from bot_app.bot import start, stop_command_handler, ACTIVE_WORKERS
from conftest import create_mock_update

@pytest.mark.asyncio
async def test_start_command(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    update = create_mock_update(user, chat, text="/start")

    with patch("bot_app.bot.set_users_blocked_status", new_callable=AsyncMock) as mock_set_status:
        await start(update, mock_context)

        mock_set_status.assert_called_once_with(chat.id, False)
        update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_stop_command_cleanup(mock_user_and_chat, mock_context):
    user, chat = mock_user_and_chat
    update = create_mock_update(user, chat, text="/stop")

    # اکنون MagicMock شناسایی می‌شود
    mock_task = MagicMock()
    mock_task.done.return_value = False
    ACTIVE_WORKERS[f"{user.id}_EURUSD_1h_FOREX"] = mock_task

    mock_context.user_data = {"temp": "data"}

    with patch("bot_app.bot.set_users_blocked_status", new_callable=AsyncMock) as mock_set_status:
        await stop_command_handler(update, mock_context)

        mock_task.cancel.assert_called_once()
        assert f"{user.id}_EURUSD_1h_FOREX" not in ACTIVE_WORKERS
        assert len(mock_context.user_data) == 0
        mock_set_status.assert_called_once_with(user.id, True)