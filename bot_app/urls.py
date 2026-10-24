from django.urls import path
from .views import (
    journal_dashboard,
    update_trade_note_api,
    py_backup_view,
    list_backups_view,
    restore_backup_view,
    day_trades_api_view,
    api_get_ai_analysis,
)

urlpatterns = [
    path('', journal_dashboard, name='dashboard'),
    path('api/journal/update-note',update_trade_note_api, name='update_trade_note'),
    path('api/take-backup/', py_backup_view, name='take_backup'),
    path('api/list-backups/', list_backups_view, name='list_backups'),
    path('api/restore-backup/', restore_backup_view, name='restore_backup'),
    path('api/day-trades/', day_trades_api_view, name='day_trades_api'),
    path('api/ai-analysis/', api_get_ai_analysis, name='api_ai_analysis'),
]