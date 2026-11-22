import logging
from asgiref.sync import sync_to_async

from bot_app.models import Watchlist,TelegramUser

logger = logging.getLogger(__name__)


@sync_to_async
def get_all_watchlist() -> list[Watchlist]:
    queryset = (
        Watchlist.objects.select_related("user")
        .order_by("-id")  # اگر اولین رکورد هر symbol را می‌خواهید، از "id" استفاده کنید
    )

    seen_symbols = set()
    unique_watchlists = []

    for item in queryset:
        if item.symbol not in seen_symbols:
            seen_symbols.add(item.symbol)
            unique_watchlists.append(item)

    return unique_watchlists

@sync_to_async
def save_watchlist_item(user: TelegramUser, symbol: str, timeframe: str, market_type: str) -> bool:
    """
    ثبت نماد در واچ‌لیست اختصاصی کاربر
    """
    obj, created = Watchlist.objects.get_or_create(
        user=user,
        symbol=symbol,
        time_frame=timeframe,
        market_type=market_type
    )
    if created:
        logger.info("New watchlist item added for user %s: %s | %s | %s", user.chat_id, symbol, timeframe, market_type)
    else:
        logger.info("Watchlist item already existed for user %s: %s | %s | %s", user.chat_id, symbol, timeframe,
                    market_type)
    return created


@sync_to_async
def delete_from_watchlist(symbol: str, timeframe: str, market_type: str) -> bool:
    """
    حذف یک آیتم از لیست زیرنظر
    خروجی: True در صورت حذف موفق، False در صورت عدم وجود
    """
    deleted_count, _ = Watchlist.objects.filter(
        symbol=symbol,
        time_frame=timeframe,
        market_type=market_type
    ).delete()

    if deleted_count > 0:
        logger.info("Deleted %s from watchlist (%s, %s)", symbol, timeframe, market_type)
        return True

    logger.warning("Failed to delete %s from watchlist or item not found", symbol)
    return False