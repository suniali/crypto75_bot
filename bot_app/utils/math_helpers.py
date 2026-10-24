def calculate_symbol_breakdown(trades: list) -> list:
    """تفکیک و خلاصه‌سازی عملکرد معاملات بر اساس نمادها (پشتیبانی کامل از دیکشنری و آبجکت جنگو)"""
    if not trades:
        return []

    breakdown = {}
    for t in trades:
        # استخراج ایمن مقادیر چه ترید دیکشنری باشد چه آبجکت جنگو
        if isinstance(t, dict):
            sym = t.get('symbol', 'UNKNOWN')
            profit = float(t.get('net_profit') or t.get('profit') or 0.0)
            is_win = t.get('result') == 'WIN' or profit > 0
        else:
            sym = getattr(t, 'symbol', 'UNKNOWN')
            net_p = getattr(t, 'net_profit', None)
            profit = float(net_p if net_p is not None else 0.0)
            result_val = getattr(t, 'result', '')
            is_win = (result_val == 'WIN') or (profit > 0)

        if sym not in breakdown:
            breakdown[sym] = {"count": 0, "wins": 0, "total_profit": 0.0}

        breakdown[sym]["count"] += 1
        breakdown[sym]["total_profit"] += profit
        if is_win:
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