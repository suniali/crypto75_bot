from django.urls import path
from django.contrib.auth import views as auth_views
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
    path('accounts/login/', auth_views.LoginView.as_view(
            template_name='registration/login.html',
            redirect_authenticated_user=True),
        name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('', journal_dashboard, name='dashboard'),
    path('api/journal/update-note',update_trade_note_api, name='update_trade_note'),
    path('api/take-backup/', py_backup_view, name='take_backup'),
    path('api/list-backups/', list_backups_view, name='list_backups'),
    path('api/restore-backup/', restore_backup_view, name='restore_backup'),
    path('api/day-trades/', day_trades_api_view, name='day_trades_api'),
    path('api/ai-analysis/', api_get_ai_analysis, name='api_ai_analysis'),
]