from django.contrib import admin

from bot_app.models import UserAlert, Watchlist


@admin.register(UserAlert)
class UserAlertAdmin(admin.ModelAdmin):
    list_display = ('id','chat_id','symbol','target_price','is_active','created_at')
    list_filter = ('is_active','symbol','created_at')
    search_fields = ('chat_id','symbol')

@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ('id','symbol','time_frame','market_type','created_at')
    list_filter = ('time_frame','market_type','created_at')
    search_fields = ('symbol','time_frame')