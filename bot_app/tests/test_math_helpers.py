import pytest
import numpy as np

from bot_app.utils.math_helpers import (
    calculate_trading_metrics,
    calculate_symbol_breakdown
)


@pytest.fixture
def sample_trades_mixed():
    """مجموعه‌ای از معاملات شامل سود، زیان و نقاط بزنگاه (Edge Cases)"""
    return [
        {'time': '2026-09-01 10:00', 'symbol': 'EURUSD', 'profit': 100.0},
        {'time': '2026-09-01 12:00', 'symbol': 'GBPUSD', 'profit': -50.0},
        {'time': '2026-09-02 14:00', 'symbol': 'EURUSD', 'profit': 150.0},
        {'time': '2026-09-03 16:00', 'symbol': 'XAUUSD', 'profit': -100.0},
    ]


@pytest.fixture
def sample_trades_only_wins():
    """معاملاتی که همگی همراه با سود هستند (تست تقسیم بر صفر برای Loss)"""
    return [
        {'time': '2026-09-01 10:00', 'symbol': 'EURUSD', 'profit': 50.0},
        {'time': '2026-09-01 12:00', 'symbol': 'EURUSD', 'profit': 100.0},
    ]


@pytest.fixture
def sample_trades_only_losses():
    """معاملاتی که همگی همراه با زیان هستند (تست تقسیم بر صفر برای Win)"""
    return [
        {'time': '2026-09-01 10:00', 'symbol': 'GBPUSD', 'profit': -40.0},
        {'time': '2026-09-01 12:00', 'symbol': 'GBPUSD', 'profit': -60.0},
    ]


# ------------------------------------------------------------------
# ۱. تست‌های calculate_trading_metrics
# ------------------------------------------------------------------
class TestCalculateTradingMetrics:

    def test_empty_trades_returns_default_zeros(self):
        """تست عملکرد در صورت ورودی خالی"""
        metrics = calculate_trading_metrics([])

        assert metrics["profit_factor"] == 0.0
        assert metrics["max_drawdown"] == 0.0
        assert metrics["payoff_ratio"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["sortino_ratio"] == 0.0
        assert metrics["avg_win"] == 0.0
        assert metrics["avg_loss"] == 0.0
        assert metrics["expectancy"] == 0.0

    def test_mixed_trades_metrics_calculation(self, sample_trades_mixed):
        """تست صحت محاسبات ریاضی شاخص‌های آمارگیری"""
        metrics = calculate_trading_metrics(sample_trades_mixed)

        # سود کل: 250 | زیان کل: 150 | Profit Factor = 250 / 150 = 1.67
        assert metrics["profit_factor"] == 1.67

        # میانگین سود: (100 + 150) / 2 = 125 | میانگین زیان: (50 + 100) / 2 = 75
        assert metrics["avg_win"] == 125.0
        assert metrics["avg_loss"] == 75.0

        # Payoff Ratio = 125 / 75 = 1.67
        assert metrics["payoff_ratio"] == 1.67

        # Expectancy = (100 - 50 + 150 - 100) / 4 = 25.0
        assert metrics["expectancy"] == 25.0

        # Max Drawdown: Cumulative = [100, 50, 200, 100] -> Peak = 200, Max DD = 200 - 100 = 100.0
        assert metrics["max_drawdown"] == 100.0

    def test_only_wins_handles_zero_division(self, sample_trades_only_wins):
        """تست عدم بروز خطای ZeroDivisionError زمان عدم وجود معامله زیان‌ده"""
        metrics = calculate_trading_metrics(sample_trades_only_wins)

        assert metrics["profit_factor"] == 150.0  # total_profit_gross
        assert metrics["payoff_ratio"] == 75.0  # avg_win
        assert metrics["avg_loss"] == 0.0
        assert metrics["max_drawdown"] == 0.0

    def test_only_losses_handles_zero_division(self, sample_trades_only_losses):
        """تست عدم بروز خطای ZeroDivisionError زمان عدم وجود معامله سودده"""
        metrics = calculate_trading_metrics(sample_trades_only_losses)

        assert metrics["profit_factor"] == 0.0
        assert metrics["payoff_ratio"] == 0.0
        assert metrics["avg_win"] == 0.0
        assert metrics["avg_loss"] == 50.0
        assert metrics["expectancy"] == -50.0

    def test_max_drawdown_calculation_unsorted_times(self):
        """تست مرتب‌سازی بر اساس زمان و محاسبه دقیق Max Drawdown"""
        # لیست نامرتب بر اساس زمان
        trades = [
            {'time': '2026-09-03 10:00', 'profit': -150.0},
            {'time': '2026-09-01 10:00', 'profit': 100.0},
            {'time': '2026-09-02 10:00', 'profit': 200.0},
        ]
        # روند زمانی صحیح: +100 -> +200 (اوج = 300) -> -150 (موجب افت 150 تایی)
        metrics = calculate_trading_metrics(trades)
        assert metrics["max_drawdown"] == 150.0


# ------------------------------------------------------------------
# ۲. تست‌های calculate_symbol_breakdown
# ------------------------------------------------------------------
class TestCalculateSymbolBreakdown:

    def test_empty_trades_returns_empty_list(self):
        """تست عملکرد در صورت ورودی خالی"""
        assert calculate_symbol_breakdown([]) == []

    def test_symbol_breakdown_grouping_and_sorting(self, sample_trades_mixed):
        """تست تفکیک صحیح نمادها، محاسبه وین‌ریت و سورت نزولی بر اساس سود"""
        result = calculate_symbol_breakdown(sample_trades_mixed)

        # باید ۳ نماد تفکیک شده باشند
        assert len(result) == 3

        # نماد اول باید سودآورترین نماد (EURUSD) باشد
        eurusd = result[0]
        assert eurusd["symbol"] == "EURUSD"
        assert eurusd["count"] == 2
        assert eurusd["win_rate"] == 100.0
        assert eurusd["profit"] == 250.0

        # نماد دوم GBPUSD با سود/زیان -50
        gbpusd = next(item for item in result if item["symbol"] == "GBPUSD")
        assert gbpusd["count"] == 1
        assert gbpusd["win_rate"] == 0.0
        assert gbpusd["profit"] == -50.0

        # لیست باید بر اساس سود نزولی مرتب باشد (profit: 250 > -50 > -100)
        profits = [item["profit"] for item in result]
        assert profits == sorted(profits, reverse=True)