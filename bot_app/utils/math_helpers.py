# utils/math_helpers.py
import numpy as np

def calculate_trading_metrics(trades: list) -> dict:
    """محاسبه کامل شاخص‌های عملکردی و ریسک حساب"""
    if not trades:
        return {
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "payoff_ratio": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0
        }

    profits = [t['profit'] for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [abs(p) for p in profits if p < 0]

    total_profit_gross = sum(wins)
    total_loss_gross = sum(losses)

    profit_factor = (total_profit_gross / total_loss_gross) if total_loss_gross > 0 else (total_profit_gross if total_profit_gross > 0 else 0.0)

    avg_win = sum(wins) / len(wins) if len(wins) > 0 else 0.0
    avg_loss = sum(losses) / len(losses) if len(losses) > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else (avg_win if avg_win > 0 else 0.0)

    returns = np.array(profits)
    std_dev = np.std(returns) if len(returns) > 1 else 0.0
    sharpe_ratio = (np.mean(returns) / std_dev * np.sqrt(252)) if std_dev > 0 else 0.0

    downside_returns = returns[returns < 0]
    downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0.0
    sortino_ratio = (np.mean(returns) / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

    sorted_trades = sorted(trades, key=lambda x: x['time'])
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for t in sorted_trades:
        cumulative += t['profit']
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "payoff_ratio": round(payoff_ratio, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "sortino_ratio": round(sortino_ratio, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round((sum(profits) / len(trades)), 2)
    }


def calculate_symbol_breakdown(trades: list) -> list:
    """تفکیک و خلاصه‌سازی عملکرد معاملات بر اساس نمادها"""
    if not trades:
        return []

    breakdown = {}
    for t in trades:
        sym = t['symbol']
        profit = t['profit']

        if sym not in breakdown:
            breakdown[sym] = {"count": 0, "wins": 0, "total_profit": 0.0}

        breakdown[sym]["count"] += 1
        breakdown[sym]["total_profit"] += profit
        if profit > 0:
            breakdown[sym]["wins"] += 1

    result = []
    for sym, data in breakdown.items():
        win_rate = (data["wins"] / data["count"]) * 100 if data["count"] > 0 else 0.0
        result.append({
            "symbol": sym,
            "count": data["count"],
            "win_rate": round(win_rate, 1),
            "profit": round(data["total_profit"], 2)
        })

    return sorted(result, key=lambda x: x['profit'], reverse=True)