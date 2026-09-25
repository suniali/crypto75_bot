from django.db import models

from bot_app.choices import MarketType

class TelegramUser(models.Model):
    chat_id = models.BigIntegerField(db_index=True, verbose_name="آیدی چت")
    username = models.CharField(max_length=100, null=True, blank=True)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    is_blocked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.username or self.chat_id

class UserAlert(models.Model):
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='alerts')
    symbol=models.CharField(max_length=20,verbose_name="نماد معاملاتی")
    target_price=models.DecimalField(max_digits=20, decimal_places=8,verbose_name="قیمت هدف")
    market_type=models.CharField(max_length=10,choices=MarketType.choices,default=MarketType.CRYPTO,verbose_name="نوع مارکت")
    is_active=models.BooleanField(default=True,verbose_name="فعال")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['symbol']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'symbol', 'target_price', 'market_type'],
                condition=models.Q(is_active=True),
                name='unique_active_alert'
            )
        ]

    @property
    def is_forex(self) -> bool:
        return self.market_type == MarketType.FOREX

    def __str__(self):
        return f"{self.user.chat_id} | {self.symbol} -> {self.target_price}"


class Watchlist(models.Model):
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='watchlists')
    symbol=models.CharField(max_length=20,verbose_name="نماد معاملاتی")
    time_frame=models.CharField(max_length=4,verbose_name="تایم فریم")
    market_type=models.CharField(max_length=10,choices=MarketType.choices,default=MarketType.CRYPTO,verbose_name="نوع مارکت")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")

    class Meta:
        unique_together = ('user', 'symbol', 'time_frame', 'market_type')


class TradeJournal(models.Model):
    TRADE_TYPES = [('BUY', 'Buy'), ('SELL', 'Sell')]
    RESULT_CHOICES = [('WIN', 'Win'), ('LOSS', 'Loss'), ('PENDING', 'Pending')]

    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='trades')
    symbol = models.CharField(max_length=20, verbose_name="نماد معاملاتی")
    trade_type=models.CharField(max_length=4,choices=TRADE_TYPES,verbose_name="نوع پوزیشن")
    entry_price = models.DecimalField(max_digits=20, decimal_places=8,verbose_name="قیمت ورود")
    exit_price = models.DecimalField(max_digits=20, decimal_places=8,null=True, blank=True,verbose_name="قیمت خروج")
    stop_loss = models.DecimalField(max_digits=20, decimal_places=8,null=True, blank=True,verbose_name="استاپ")
    take_profit = models.DecimalField(max_digits=20, decimal_places=8,null=True, blank=True,verbose_name="تارگت")
    result=models.CharField(max_length=10,choices=RESULT_CHOICES,default='PENDING',verbose_name="نتیجه")
    image_path = models.CharField(max_length=255, null=True, blank=True,verbose_name="مسیر عکس")
    created_at=models.DateTimeField(auto_now_add=True,verbose_name="تاریخ ثبت")

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["symbol"]),
        ]

    @property
    def risk_reward(self):
        if self.stop_loss and self.take_profit and self.entry_price:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.take_profit - self.entry_price)
            if risk > 0:
                return round(reward / risk, 2)
        return 0.0