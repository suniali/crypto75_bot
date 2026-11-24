from __future__ import annotations
from typing import Optional
from .config import NDSConfig
from .structure import Setup


def _fmt(x: float) -> str:
    return f"{x:.5f}".rstrip("0").rstrip(".") if abs(x) < 1000 else f"{x:.2f}"


def explain(s: Setup, cfg: NDSConfig, prob: Optional[float] = None, thr: Optional[float] = None,
            symbol: str = "", lang: str = "fa") -> str:
    f = _fmt
    sell = s.direction == -1
    p1, p2, p3 = (x.price for x in s.p)
    o1, o2 = (x.price for x in s.o)
    hn = [x.price for x in s.htf.nodes]
    ho = [x.price for x in s.htf.opens]

    risk = abs(s.entry - s.sl)
    rr1 = abs(s.entry - s.tp1) / risk
    rr_final = abs(s.entry - s.tp_final) / risk

    ai = ""
    if prob is not None:
        ai = f"AI Meta-Filter: P(win)={prob:.0%}" + (f" (threshold {thr:.0%})" if thr is not None else "")

    side = "فروش (SELL)" if sell else "خرید (BUY)"
    pk = "قله‌های صعودی" if sell else "دره‌های نزولی"
    op = "دره‌های" if sell else "قله‌های"
    reentry_tag = " [ورود دوم روی تراز ۸۶.۴٪]" if s.is_reentry_864 else " [ورود اول سکانس]"

    lines = [
        f"🔔 NDS {side}{reentry_tag} | {symbol} | ورود: {f(s.entry)} | {s.time:%Y-%m-%d %H:%M}",
        f"🛑 حد ضرر (پشت اسپایک F): {f(s.sl)}",
        f"🎯 هدف ۱ (نقاط ۱ و ۲ باز - خروج ۱/۳ حجم): {f(s.tp1)} (R:R = 1:{rr1:.2f})",
        f"🏁 هدف نهایی (نود ۳ تایم‌فریم {cfg.htf}): {f(s.tp_final)} (R:R = 1:{rr_final:.2f})",
        "",
        "چرا این سیگنال صادر شد؟",
        f"۱) جهت کلان ({cfg.htf}): سه {pk} N1={f(hn[0])}، N2={f(hn[1])}، N3={f(hn[2])} شکل گرفته و {op} باز {f(ho[0])} و {f(ho[1])} هنوز بسته نشده‌اند (Hurst={s.htf.hurst:.2f}).",
        f"۲) ساختار ({cfg.ltf}): نودهای ۱-۲-۳ = {f(p1)} / {f(p2)} / {f(p3)} و نقاط باز ۱ و ۲ = {f(o1)} و {f(o2)}.",
        f"۳) تایید BABI: پس از پولبک Z={f(s.z)} و اسپایک F={f(s.f)} فراتر از نود ۳، کندل داخل نود ۳ بسته شد.",
        "۴) اولویت سکانس: به محض بسته شدن نقاط ۱ و ۲ باز با نود ۳ جدید، ۱/۳ حجم خارج می‌شود و منتظر تراز ۸۶.۴٪ نمی‌مانیم.",
    ]
    if s.lvl864 == s.lvl864:
        lines.append(f"۵) تراز مرجع ۸۶.۴٪ لگ قبلی برای شکار ستاپ بعدی: {f(s.lvl864)}")
    if ai:
        lines.append(f"🤖 {ai}")
    lines.append("⚠️ فقط سیگنال تحلیلی است؛ معامله خودکار باز نمی‌شود.")
    return "\n".join(lines)