from django.db import models
from decimal import Decimal
from django.core.validators import MinValueValidator,MaxValueValidator

class MarketType(models.TextChoices):
    CRYPTO = 'CRYPTO', 'کریپتوکارنسی'
    FOREX = 'FOREX', 'فارکس'


# ==========================================
# 1. مدل کاربر تلگرام (TelegramUser)
# ==========================================
class TelegramUser(models.Model):
    chat_id = models.BigIntegerField(unique=True, db_index=True, verbose_name="آیدی چت تلگرام")
    username = models.CharField(max_length=100, null=True, blank=True, verbose_name="نام کاربری")
    first_name = models.CharField(max_length=100, null=True, blank=True, verbose_name="نام")
    is_blocked = models.BooleanField(default=False, verbose_name="بلاک شده")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ عضویت")

    class Meta:
        verbose_name = "کاربر تلگرام"
        verbose_name_plural = "کاربران تلگرام"
        ordering = ['-created_at']

    def __str__(self):
        return f"@{self.username}" if self.username else str(self.chat_id)


# ==========================================
# 2. تنظیمات اختصاصی کاربر (UserSetting)
# ==========================================
class UserSetting(models.Model):
    user = models.OneToOneField(TelegramUser, on_delete=models.CASCADE, related_name='setting', verbose_name="کاربر")
    initial_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('1000.00'),
                                          verbose_name="بالانس اولیه حساب ($)")
    default_risk_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'),
                                               verbose_name="درصد ریسک در هر معامله (%)")
    default_leverage = models.IntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(125)],
                                           verbose_name="اهرم/لوریج پیش‌فرض")
    receive_daily_reports = models.BooleanField(default=True, verbose_name="دریافت گزارش‌های روزانه")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخرین بروزرسانی")

    class Meta:
        verbose_name = "تنظیمات کاربر"
        verbose_name_plural = "تنظیمات کاربران"

    def __str__(self):
        return f"تنظیمات {self.user}"


# ==========================================
# 3. مدل هشدارهای قیمت (UserAlert)
# ==========================================
class UserAlert(models.Model):
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='alerts', verbose_name="کاربر")
    symbol = models.CharField(max_length=20, verbose_name="نماد معاملاتی")
    target_price = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="قیمت هدف")
    market_type = models.CharField(max_length=10, choices=MarketType.choices, default=MarketType.CRYPTO,
                                   verbose_name="نوع مارکت")
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    is_triggered = models.BooleanField(default=False, verbose_name="فعال‌شده / هشدار داده‌شده")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ثبت")

    class Meta:
        verbose_name = "هشدار قیمت"
        verbose_name_plural = "هشدارهای قیمت"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['symbol', 'is_active']),
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
        return f"{self.user} | {self.symbol} -> {self.target_price:.4f}"


# ==========================================
# 4. مدل دیده‌بان نمادها (Watchlist)
# ==========================================
class Watchlist(models.Model):
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='watchlists', verbose_name="کاربر")
    symbol = models.CharField(max_length=20, verbose_name="نماد معاملاتی")
    time_frame = models.CharField(max_length=10, default='1h', verbose_name="تایم فریم")
    market_type = models.CharField(max_length=10, choices=MarketType.choices, default=MarketType.CRYPTO,
                                   verbose_name="نوع مارکت")
    is_active = models.BooleanField(default=True, verbose_name="فعال")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ثبت")

    class Meta:
        verbose_name = "دیده‌بان"
        verbose_name_plural = "دیده‌بان‌ها"
        ordering = ['-created_at']
        unique_together = ('user', 'symbol', 'time_frame', 'market_type')

    def __str__(self):
        return f"{self.user} | {self.symbol} ({self.time_frame})"


# ==========================================
# 5. مدل ژورنال معاملات (TradeJournal)
# ==========================================
class ActiveTradeManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class TradeJournal(models.Model):
    TRADE_TYPES = [('BUY', 'Buy'), ('SELL', 'Sell')]
    RESULT_CHOICES = [
        ('WIN', 'Win'),
        ('LOSS', 'Loss'),
        ('BE', 'Break Even'),
        ('PENDING', 'Pending'),
    ]

    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='trades', verbose_name="کاربر")
    symbol = models.CharField(max_length=20, verbose_name="نماد معاملاتی")
    market_type = models.CharField(max_length=10, choices=MarketType.choices, default=MarketType.CRYPTO,
                                   verbose_name="نوع مارکت")
    trade_type = models.CharField(max_length=4, choices=TRADE_TYPES, verbose_name="نوع پوزیشن")

    # حجمی و قیمتی
    volume = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.01'),
                                 verbose_name="حجم (لات/تعداد)")
    entry_price = models.DecimalField(max_digits=20, decimal_places=8, verbose_name="قیمت ورود")
    exit_price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True, verbose_name="قیمت خروج")
    stop_loss = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True, verbose_name="حد ضرر (SL)")
    take_profit = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True,
                                      verbose_name="حد سود (TP)")

    # سود و هزینه‌ها
    profit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'),
                                 verbose_name="سود/زیان خام ($)")
    commission = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
                                     verbose_name="کمیسیون ($)")
    swap = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="سوآپ ($)")

    result = models.CharField(max_length=10, choices=RESULT_CHOICES, default='PENDING', verbose_name="نتیجه")
    notes = models.TextField(null=True, blank=True, verbose_name="توضیحات/استراتژی")
    image = models.ImageField(upload_to='trade_charts/%Y/%m/', null=True, blank=True, verbose_name="تصویر چارت")

    # کنترل ادمین
    is_active = models.BooleanField(default=True, verbose_name="فعال / شامل در محاسبات")

    # زمان‌بندی
    entry_time = models.DateTimeField(null=True, blank=True, verbose_name="زمان ورود")
    exit_time = models.DateTimeField(null=True, blank=True, verbose_name="زمان خروج")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاریخ ثبت سیستم")

    objects = models.Manager()
    active_objects = ActiveTradeManager()

    class Meta:
        verbose_name = "ژورنال معامله"
        verbose_name_plural = "ژورنال‌های معاملاتی"
        ordering = ['-entry_time', '-created_at']
        indexes = [
            models.Index(fields=["user", "is_active", "entry_time"]),
            models.Index(fields=["symbol"]),
            models.Index(fields=["result"]),
        ]

    @property
    def net_profit(self) -> float:
        """محاسبه سود/زیان خالص نهایی"""
        return float(self.profit + self.commission + self.swap)

    @property
    def planned_risk_reward(self) -> float:
        """ریسک به ریوارد برنامه ریزی شده"""
        if self.stop_loss and self.take_profit and self.entry_price:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.take_profit - self.entry_price)
            if risk > 0:
                return round(float(reward / risk), 2)
        return 0.0

    @property
    def realized_risk_reward(self) -> float:
        """ریسک به ریوارد واقعی محقق شده براساس قیمت خروج"""
        if self.stop_loss and self.exit_price and self.entry_price:
            risk = abs(self.entry_price - self.stop_loss)
            realized_reward = self.exit_price - self.entry_price if self.trade_type == 'BUY' else self.entry_price - self.exit_price
            if risk > 0:
                return round(float(realized_reward / risk), 2)
        return 0.0

    def to_dict(self) -> dict:
        """تبدیل به دیکشنری برای توابع چارت و Gemini"""
        return {
            'id': self.id,
            'symbol': self.symbol,
            'trade_type': self.trade_type,
            'profit': float(self.profit),
            'commission': float(self.commission),
            'swap': float(self.swap),
            'net_profit': self.net_profit,
            'result': self.result,
            'is_active': self.is_active,
            'time': self.entry_time.isoformat() if self.entry_time else self.created_at.isoformat(),
            'rr': self.realized_risk_reward or self.planned_risk_reward
        }

    def __str__(self):
        return f"{self.user} | {self.symbol} ({self.trade_type}) | PnL: ${self.net_profit:,.2f}"