"""Human readable reasoning for every signal (Persian default, English optional)."""
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
    rr1 = abs(s.entry - s.tp1) / abs(s.entry - s.sl)
    rr2 = abs(s.entry - s.tp2) / abs(s.entry - s.sl)
    ai = ""
    if prob is not None:
        ai = (f"AI: P(win)={prob:.0%}" + (f" (threshold {thr:.0%})" if thr is not None else ""))
    if lang == "en":
        side = "SELL" if sell else "BUY"
        kind = ("rising peaks", "valleys") if sell else ("falling valleys", "peaks")
        lines = [
            f"NDS {side} {symbol} @ {f(s.entry)}  [{s.time:%Y-%m-%d %H:%M}]",
            f"SL {f(s.sl)} | TP1 {f(s.tp1)} (close {cfg.tp1_fraction:.0%}) | TP2 {f(s.tp2)} (rest) | R:R 1:{rr1:.2f} / 1:{rr2:.2f}",
            f"WHY: HTF {cfg.htf}: three {kind[0]} N1 {f(hn[0])} / N2 {f(hn[1])} / N3 {f(hn[2])}; open {kind[1]} {f(ho[0])}, {f(ho[1])} not closed yet -> {side} setups only.",
            f"LTF {cfg.ltf}: nodes 1-2-3 = {f(p1)} / {f(p2)} / {f(p3)}, open nodes {f(o1)} and {f(o2)}.",
            f"Flag: pullback Z {f(s.z)}, spike F {f(s.f)} beyond node 3; a bar closed back inside node 3 (BABI) = exhaustion.",
            f"Levels: SL behind flag F + {cfg.sl_buffer_atr} ATR; TP1 = LTF open nodes; TP2 = HTF target.",
        ]
        if s.lvl864 == s.lvl864:
            lines.append(f"Cycle 86.4% level: {f(s.lvl864)}")
        if ai:
            lines.append(ai)
        return "\n".join(lines)

    side = "فروش (SELL)" if sell else "خرید (BUY)"
    pk = "قله‌های صعودی" if sell else "دره‌های نزولی"
    op = "دره‌های" if sell else "قله‌های"
    lines = [
        f"🔔 NDS {side} | {symbol} | قیمت ورود {f(s.entry)} | {s.time:%Y-%m-%d %H:%M}",
        f"حد ضرر: {f(s.sl)} | هدف ۱: {f(s.tp1)} (خروج {cfg.tp1_fraction:.0%} حجم) | هدف ۲: {f(s.tp2)} (بقیه حجم) | R:R = 1:{rr1:.2f} و 1:{rr2:.2f}",
        "",
        "چرا این سیگنال صادر شد؟",
        f"۱) جهت (تایم‌فریم {cfg.htf}): سه {pk} N1={f(hn[0])}، N2={f(hn[1])}، N3={f(hn[2])} شکل گرفته و {op} باز {f(ho[0])} و {f(ho[1])} هنوز بسته نشده‌اند؛ پس NDS فقط معاملهٔ {side} را مجاز می‌داند.",
        f"۲) ستاپ (تایم‌فریم {cfg.ltf}): نودهای ۱-۲-۳ = {f(p1)} / {f(p2)} / {f(p3)} و نودهای باز {f(o1)} و {f(o2)}.",
        f"۳) فلگ: بعد از نود ۳ قیمت تا Z={f(s.z)} اصلاح کرد (بدون بستن نودهای باز) و اسپایک تا F={f(s.f)} از نود ۳ عبور کرد؛ سپس یک کندل داخل نود ۳ برگشت و بست (BABI/ریجکت) = پایان تلاش حرکت.",
        f"۴) سطوح: استاپ پشت فلگ (F ± {cfg.sl_buffer_atr} ATR). هدف ۱ = نودهای باز تایم‌فریم پایین. هدف ۲ = هدف نهایی تایم‌فریم بالا.",
    ]
    if s.lvl864 == s.lvl864:
        lines.append(f"۵) سطح بستن سایکل ۸۶.۴٪ برای مرجع: {f(s.lvl864)}")
    if ai:
        lines.append(f"🤖 {ai}")
    lines.append("⚠️ فقط سیگنال است؛ هیچ معامله‌ای خودکار باز نمی‌شود.")
    return "\n".join(lines)
