from django.db import models

class UserAlert(models.Model):
    chat_id=models.CharField(max_length=250,verbose_name="آیدی چت تلگرام")
    symbol=models.CharField(max_length=20,verbose_name="نماد معاملاتی")
    target_price=models.FloatField(verbose_name="قیمت هدف")
    is_active=models.BooleanField(default=True,verbose_name="فعال")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")

    def __str__(self):
        return f"{self.chat_id} | {self.symbol} -> {self.target_price}"
