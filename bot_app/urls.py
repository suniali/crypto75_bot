from django.urls import path
from .views import journal_dashboard,update_trade_note_api,py_backup_view

urlpatterns = [
    path('', journal_dashboard, name='dashboard'),
    path('api/journal/update-note',update_trade_note_api, name='update_trade_note'),
    path('api/take-backup/', py_backup_view, name='take_backup')
]