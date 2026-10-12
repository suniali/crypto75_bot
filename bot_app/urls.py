from django.urls import path
from .views import (
    journal_dashboard,
    update_trade_note_api,
    py_backup_view,
    list_backups_view,
    restore_backup_view
)

urlpatterns = [
    path('', journal_dashboard, name='dashboard'),
    path('api/journal/update-note',update_trade_note_api, name='update_trade_note'),
    path('api/take-backup/', py_backup_view, name='take_backup'),
    path('api/list-backups/', list_backups_view, name='list_backups'),
    path('api/restore-backup/', restore_backup_view, name='restore_backup'),
]