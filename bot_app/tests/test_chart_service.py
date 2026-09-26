import io
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date
from unittest.mock import patch

from bot_app.services.chart_service import (
    calculate_heikin_ashi,
    create_heikin_ashi_chart,
    create_pending_alert_chart,
    generate_pro_daily_dashboard,
    calculate_today_stats,
)


@pytest.fixture
def sample_ohlc_df():
    """دیتافرایم نمونه کندل‌های قیمت"""
    dates = pd.date_range(start="2026-01-01", periods=5, freq="1h")
    data = {
        'Open': [100.0, 102.0, 101.0, 105.0, 104.0],
        'High': [103.0, 104.0, 106.0, 107.0, 108.0],
        'Low': [99.0, 100.0, 100.0, 103.0, 102.0],
        'Close': [102.0, 101.0, 105.0, 104.0, 107.0],
        'Volume': [1000, 1200, 1100, 1500, 1300]
    }
    return pd.DataFrame(data, index=dates)


class TestChartService:

    # ------------------------------------------------------------------
    # 1. Tests for Heikin-Ashi Calculation
    # ------------------------------------------------------------------
    def test_calculate_heikin_ashi(self, sample_ohlc_df):
        """تست فرمول‌های ریاضی و خروجی ساختار کندل‌های هیکن‌اشی"""
        ha_df = calculate_heikin_ashi(sample_ohlc_df)

        assert isinstance(ha_df, pd.DataFrame)
        assert set(['Open', 'High', 'Low', 'Close']).issubset(ha_df.columns)
        assert len(ha_df) == len(sample_ohlc_df)

        # تست فرمول HA Close: (Open + High + Low + Close) / 4 برای کندل اول
        expected_close_0 = (100.0 + 103.0 + 99.0 + 102.0) / 4
        assert np.isclose(ha_df['Close'].iloc[0], expected_close_0)

        # تست فرمول HA Open برای کندل اول: (Open0 + Close0) / 2
        expected_open_0 = (100.0 + 102.0) / 2
        assert np.isclose(ha_df['Open'].iloc[0], expected_open_0)

        # تست فرمول HA Open برای کندل دوم: (HA_Open0 + HA_Close0) / 2
        expected_open_1 = (expected_open_0 + expected_close_0) / 2
        assert np.isclose(ha_df['Open'].iloc[1], expected_open_1)

    # ------------------------------------------------------------------
    # 2. Tests for Alert Charts
    # ------------------------------------------------------------------
    def test_create_heikin_ashi_chart(self, sample_ohlc_df):
        """تست ساخت چارت آلرت فعال‌شده و تولید تصویر PNG"""
        buf = create_heikin_ashi_chart(sample_ohlc_df, target_price=105.0, symbol="EURUSD")

        assert buf is not None
        assert isinstance(buf, io.BytesIO)
        assert buf.getvalue().startswith(b'\x89PNG')  # تایید بایت‌های فریم PNG

    def test_create_pending_alert_chart(self, sample_ohlc_df):
        """تست ساخت چارت آلرت در انتظار"""
        buf = create_pending_alert_chart(sample_ohlc_df, target_price=100.0, symbol="BTCUSDT")

        assert buf is not None
        assert isinstance(buf, io.BytesIO)
        assert buf.getvalue().startswith(b'\x89PNG')

    # ------------------------------------------------------------------
    # 3. Tests for Daily Performance Dashboard
    # ------------------------------------------------------------------
    def test_generate_pro_daily_dashboard_with_data(self):
        """تست رندر کامل داشبورد عملکرد روزانه در صورت وجود معامله"""
        buf = generate_pro_daily_dashboard(
            wins=3,
            losses=1,
            net_profit=150.0,
            win_rate=75.0,
            avg_win=60.0,
            avg_loss=30.0,
            profits_history=[50.0, -30.0, 70.0, 60.0]
        )

        assert buf is not None
        assert isinstance(buf, io.BytesIO)
        assert buf.getvalue().startswith(b'\x89PNG')

    def test_generate_pro_daily_dashboard_zero_trades(self):
        """تست عدم وقوع خطا و رندر موفق داشبورد در صورت عدم وجود معامله"""
        buf = generate_pro_daily_dashboard(
            wins=0,
            losses=0,
            net_profit=0.0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profits_history=[0.0]
        )

        assert buf is not None
        assert isinstance(buf, io.BytesIO)
        assert buf.getvalue().startswith(b'\x89PNG')

    # ------------------------------------------------------------------
    # 4. Tests for Today Stats Calculation
    # ------------------------------------------------------------------
    def test_calculate_today_stats_with_mixed_time_formats(self):
        """تست محاسبات آمار امروز با انواع فرمت‌های زمانی (datetime, timestamp, string)"""
        today_str = date.today().strftime("%Y.%m.%d")
        today_dt = datetime.now()
        today_ts = datetime.now().timestamp()

        closed_deals = [
            {"profit": 100.0, "close_time": today_dt},
            {"profit": -40.0, "time": today_ts},
            {"profit": 50.0, "date": f"{today_str} 14:30:00"},
            {"profit": -10.0, "close_time": "2020.01.01 10:00:00"}  # معامله قدیمی که باید نادیده گرفته شود
        ]

        total_trades, win_count, loss_count, win_rate, net_profit, avg_win, avg_loss, profits_history = (
            calculate_today_stats(closed_deals)
        )

        assert total_trades == 3
        assert win_count == 2
        assert loss_count == 1
        assert np.isclose(win_rate, (2 / 3) * 100.0)
        assert net_profit == 110.0  # 100 - 40 + 50
        assert avg_win == 75.0      # (100 + 50) / 2
        assert avg_loss == 40.0     # abs(-40) / 1
        assert profits_history == [100.0, -40.0, 50.0]

    def test_calculate_today_stats_empty_deals(self):
        """تست محاسبه آمار هنگامی که لیست معاملات خالی است"""
        total_trades, win_count, loss_count, win_rate, net_profit, avg_win, avg_loss, profits_history = (
            calculate_today_stats([])
        )

        assert total_trades == 0
        assert win_count == 0
        assert loss_count == 0
        assert win_rate == 0.0
        assert net_profit == 0.0
        assert avg_win == 0.0
        assert avg_loss == 0.0
        assert profits_history == [0.0]

    def test_calculate_today_stats_fallback_date(self):
        """تست فال‌بک وقتی فرمت تاریخ ناشناخته است و به عنوان معامله امروز فرض می‌شود"""
        closed_deals = [
            {"profit": 80.0, "close_time": "UNKNOWN_DATE_FORMAT"}
        ]

        total_trades, win_count, loss_count, win_rate, net_profit, avg_win, avg_loss, profits_history = (
            calculate_today_stats(closed_deals)
        )

        assert total_trades == 1
        assert net_profit == 80.0