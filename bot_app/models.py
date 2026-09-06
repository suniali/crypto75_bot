from django.db import models

class UserAlert(models.Model):
    MARKET_CHOICES = [('CRYPTO', 'Crypto'), ('FOREX', 'Forex')]

    chat_id=models.CharField(max_length=50,verbose_name="آیدی چت تلگرام")
    symbol=models.CharField(max_length=20,verbose_name="نماد معاملاتی")
    target_price=models.FloatField(verbose_name="قیمت هدف")
    market_type=models.CharField(max_length=10,choices=MARKET_CHOICES,default='CRYPTO',verbose_name="نوع مارکت")
    is_active=models.BooleanField(default=True,verbose_name="فعال")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")

    @property
    def is_forex(self):
        return self.market_type.lower() == 'forex'

    def __str__(self):
        return f"{self.chat_id} | {self.symbol} -> {self.target_price}"

class TradeJournal(models.Model):
    TRADE_TYPES = [('BUY', 'Buy'), ('SELL', 'Sell')]
    RESULT_CHOICES = [('WIN', 'Win'), ('LOSS', 'Loss'), ('PENDING', 'Pending')]

    chat_id = models.CharField(max_length=50, verbose_name="آیدی چت تلگرام")
    symbol = models.CharField(max_length=20, verbose_name="نماد معاملاتی")
    trade_type=models.CharField(max_length=4,choices=TRADE_TYPES,verbose_name="نوع پوزیشن")
    entry_price = models.FloatField(verbose_name="قیمت ورود")
    exit_price = models.FloatField(null=True, blank=True,verbose_name="قیمت خروج")
    stop_loss = models.FloatField(null=True, blank=True,verbose_name="استاپ")
    take_profit = models.FloatField(null=True, blank=True,verbose_name="تارگت")
    risk_reward = models.FloatField(default=0.0,verbose_name="ریسک به ریوارد")
    result=models.CharField(max_length=10,choices=RESULT_CHOICES,default='PENDING',verbose_name="نتیجه")
    image_path = models.CharField(max_length=255, null=True, blank=True,verbose_name="مسیر عکس")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")