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

@admin.register(TradeJournal)
class TradeJournalAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'symbol', 'trade_type',
        'entry_price', 'exit_price', 'result',
        'risk_reward', 'created_at'
    )
    list_filter = ('trade_type', 'result', 'created_at')
    search_fields = ('user__chat_id', 'user__username', 'symbol')
    autocomplete_fields = ('user',)
    readonly_fields = ('risk_reward',)