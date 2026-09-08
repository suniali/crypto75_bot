import requests
import pandas as pd
import pandas_ta as ta
import httpx

from bot_app.mt5_service import get_data_for_rsi

async def calculate_rsi(symbol,timeframe,market_type="CRYPTO"):
    if market_type == "CRYPTO":
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=100"
        async with httpx.AsyncClient() as client:
            res=(await client.get(url)).json()

        if not res:
            return None, 'داده ای یافت نشد!'

        df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'volume', '_', '_', '_', '_', '_', '_'])
        df['close'] = df['close'].astype(float)

    elif market_type == "FOREX":
        rates, msg=get_data_for_rsi(symbol,timeframe)
        if rates is None:
            return None, msg

        df=pd.DataFrame(rates)

    else:
        return None, "مارکت یافت نشد!"

    # محاسبه RSI با pandas-ta
    df['rsi'] = ta.rsi(df['close'], length=14)
    latest_rsi = df['rsi'].iloc[-1]

    status = "NORMAL"
    if latest_rsi >= 70:
        status = "OVERBOUGHT (اشباع خرید - احتمال ریزش)"
    elif latest_rsi <= 30:
        status = "OVERSOLD (اشباع فروش - احتمال صعود)"

    return round(latest_rsi, 2), status
