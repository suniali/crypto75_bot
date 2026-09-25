from django.db import models

class MarketType(models.TextChoices):
    CRYPTO = 'CRYPTO', 'Crypto'
    FOREX = 'FOREX', 'Forex'