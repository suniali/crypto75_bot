from django.contrib import admin

from bot_app.models import UserAlert, Watchlist, TelegramUser, TradeJournal


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('id','chat_id','username','first_name','is_blocked','created_at')
    list_filter = ('is_blocked', 'created_at')
    search_fields = ('chat_id', 'username', 'first_name')

@admin.register(UserAlert)
class UserAlertAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'symbol', 'target_price', 'is_active', 'created_at')
    list_filter = ('is_active', 'market_type', 'created_at')
    search_fields = ('user__chat_id', 'user__username', 'symbol')
    autocomplete_fields = ('user',)

@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ('id','user','symbol','time_frame','market_type','created_at')
    list_filter = ('time_frame','market_type','created_at')
    search_fields = ('user__chat_id','symbol','time_frame')


@admin.action(description='✅ فعال‌سازی معاملات انتخاب‌شده (شامل شدن در آمار)')
def make_active(modeladmin, request, queryset):
    queryset.update(is_active=True)


@admin.action(description='❌ غیرفعال‌سازی معاملات انتخاب‌شده (حذف از آمار و گزارش‌ها)')
def make_inactive(modeladmin, request, queryset):
    queryset.update(is_active=False)


@admin.register(TradeJournal)
class TradeJournalAdmin(admin.ModelAdmin):
    # ستون‌های نمایشی در جدول ادمین
    list_display = [
        'id', 'user', 'symbol', 'trade_type', 'volume',
        'entry_price', 'exit_price', 'net_profit_display',
        'result', 'is_active', 'entry_time'
    ]

    # فیلترهای سمت راست پنل
    list_filter = ['is_active', 'result', 'trade_type', 'symbol', 'created_at']

    # قابلیت جستجو
    search_fields = ['symbol', 'user__username', 'user__telegram_id', 'notes']

    # ویرایش سریع مستقیم از روی جدول (بدون باز کردن صفحه)
    list_editable = ['is_active', 'result']

    # اکشن‌های گروهی برای ادمین
    actions = [make_active, make_inactive]

    # تعداد آیتم در هر صفحه
    list_per_page = 25

    # نمایش زیباتر سود خالص در ادمین
    @admin.display(description='سود خالص ($)')
    def net_profit_display(self, obj):
        val = obj.net_profit
        color = 'green' if val >= 0 else 'red'
        return f"${val:,.2f}"