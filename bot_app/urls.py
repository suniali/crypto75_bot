from django.urls import path
from .views import journal_dashboard

urlpatterns = [
    path('', journal_dashboard, name='dashboard'), # مسیر روت اپلیکیشن
]