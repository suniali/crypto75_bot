import io
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from PIL import Image
from asgiref.sync import sync_to_async

from bot_app.models import TelegramUser, TradeJournal
from bot_app.services.journal_service import (
    sync_open_trade_to_db,
    finalize_closed_trade_in_db,
)

pytestmark = pytest.mark.django_db


@pytest_asyncio.fixture
async def mock_user():
    user, _ = await sync_to_async(TelegramUser.objects.get_or_create)(
        chat_id=123456789, defaults={"username": "test_trader"}
    )
    return user


@pytest.fixture
def dummy_chart_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="black")
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@pytest.mark.asyncio
async def test_sync_open_trade_creates_pending_record(mock_user):
    """تست ثبت اولیه معامله (وضعیت باید PENDING باشد و مجدداً آپدیت نشود)"""
    pos_data = {
        "ticket": 1001,
        "symbol": "EURUSD",
        "type": "BUY",
        "volume": 0.1,
        "price_open": 1.08500,
        "sl": 1.08000,
        "tp": 1.09000,
        "time": int(datetime.now(timezone.utc).timestamp()),
    }

    await sync_open_trade_to_db(chat_id=mock_user.chat_id, pos_data=pos_data)

    trade = await TradeJournal.objects.filter(position_id=1001).afirst()
    assert trade is not None
    assert trade.result == "PENDING"  # نشان‌دهنده معامله باز
    assert float(trade.stop_loss) == 1.08000

    # تست عدم تغییر SL در فرستادن مجدد داده
    pos_data["sl"] = 1.07000
    await sync_open_trade_to_db(chat_id=mock_user.chat_id, pos_data=pos_data)

    trade_reload = await TradeJournal.objects.filter(position_id=1001).afirst()
    assert float(trade_reload.stop_loss) == 1.08000


@pytest.mark.asyncio
async def test_finalize_closed_trade_win_with_chart(mock_user, dummy_chart_bytes):
    """تست بستن معامله سودده و تغییر وضعیت از PENDING به WIN"""
    position_id = 1004

    pos_data = {
        "ticket": position_id,
        "symbol": "EURUSD",
        "type": "BUY",
        "volume": 1.0,
        "price_open": 1.08000,
        "sl": 1.07500,
        "tp": 1.09000,
        "time": int(datetime.now(timezone.utc).timestamp()),
    }
    await sync_open_trade_to_db(chat_id=mock_user.chat_id, pos_data=pos_data)

    closed_deal_data = {
        "position_id": position_id,
        "symbol": "EURUSD",
        "volume": 1.0,
        "entry_price": 1.08000,
        "exit_price": 1.09000,
        "profit": 100.0,
        "commission": -7.0,
        "swap": -1.5,
        "time": int(datetime.now(timezone.utc).timestamp()),
    }

    await finalize_closed_trade_in_db(
        chat_id=mock_user.chat_id,
        deal=closed_deal_data,
        chart_buf=dummy_chart_bytes
    )

    trade = await TradeJournal.objects.filter(position_id=position_id).afirst()
    assert trade.result == "WIN"  # تعیین نتیجه براساس سود مثبت
    assert float(trade.profit) == 100.0
    assert bool(trade.image) is True


@pytest.mark.asyncio
async def test_finalize_closed_trade_loss_without_prior_open_record(mock_user):
    """تست بستن معامله زیان‌دهی که قبل‌تر ثبت نشده بود و تغییر نتیجه به LOSS"""
    position_id = 1005

    closed_deal_data = {
        "position_id": position_id,
        "symbol": "BTCUSD",
        "volume": 0.05,
        "entry_price": 60000.0,
        "exit_price": 58000.0,
        "profit": -100.0,
        "commission": -2.0,
        "swap": 0.0,
        "time": int(datetime.now(timezone.utc).timestamp()),
    }

    await finalize_closed_trade_in_db(
        chat_id=mock_user.chat_id,
        deal=closed_deal_data,
        chart_buf=None
    )

    trade = await TradeJournal.objects.filter(position_id=position_id).afirst()
    assert trade is not None
    assert trade.result == "LOSS"  # تعیین نتیجه براساس سود منفی