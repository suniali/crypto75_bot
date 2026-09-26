import os
import io

from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from bot_app.services.analysis_service import generate_report_charts
from bot_app.utils.math_helpers import calculate_trading_metrics, calculate_symbol_breakdown
from bot_app.utils.text_helpers import reshape_fa, reshape_multiline_fa


# ------------------------------------------------------------------
# ثبت فونت وزیر به همراه مدیریت مسیر چندلایه
# ------------------------------------------------------------------
SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SERVICES_DIR))
FONT_PATH = os.path.join(ROOT_DIR, "Vazirmatn-Regular.ttf")

if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('Vazir', FONT_PATH))
    print(f"✅ فونت با موفقیت بارگذاری شد: {FONT_PATH}")
else:
    print(f"⚠️ warning: {FONT_PATH} not found. Falling back to Helvetica.")


def _safe_float(value, default=0.0) -> float:
    """تبدیل ایمن مقادیر به float جهت جلوگیری از خطای فرمت‌دهی رشته‌ها"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def generate_pdf_report(filename: str, period_name: str, trades: list, ai_analysis: str):
    """تولید گزارش PDF استاندارد و شکیل با پشتیبانی ۱۰۰٪ از متون فارسی و نمودارها"""

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25
    )
    story = []
    charts = {}

    styles = getSampleStyleSheet()
    font_name = 'Vazir' if 'Vazir' in pdfmetrics.getRegisteredFontNames() else 'Helvetica'

    body_style = ParagraphStyle(
        'FaBody',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=8.5,
        leading=13,
        alignment=2,  # Right aligned (راست‌چین)
        textColor=colors.HexColor("#212121")
    )

    title_style = ParagraphStyle(
        'FaTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=14,
        leading=18,
        alignment=1,  # Center aligned (وسط‌چین)
        textColor=colors.HexColor("#0D47A1")
    )

    section_title_style = ParagraphStyle(
        'FaSectionTitle',
        parent=styles['Heading2'],
        fontName=font_name,
        fontSize=10,
        leading=14,
        alignment=2,  # Right aligned
        textColor=colors.HexColor("#1A237E")
    )

    try:
        # ------------------------------------------------------------------
        # ۱. عنوان اصلی
        # ------------------------------------------------------------------
        title_text = reshape_fa(f"گزارش جامع و تخصصی عملکرد معاملات ({period_name})")
        story.append(Paragraph(f"<b>{title_text}</b>", title_style))
        story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۲. خلاصه آمار و شاخص‌های وال‌استریت
        # ------------------------------------------------------------------
        metrics = calculate_trading_metrics(trades) if trades else {}
        total_trades = len(trades) if trades else 0
        total_profit = sum(_safe_float(t.get('profit')) for t in (trades or []))
        win_trades = sum(1 for t in (trades or []) if _safe_float(t.get('profit')) > 0)
        win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0.0

        sharpe = _safe_float(metrics.get('sharpe_ratio'))
        profit_factor = _safe_float(metrics.get('profit_factor'))
        payoff = _safe_float(metrics.get('payoff_ratio'))
        max_dd = _safe_float(metrics.get('max_drawdown'))
        expectancy = _safe_float(metrics.get('expectancy'))
        avg_win = _safe_float(metrics.get('avg_win'))
        avg_loss = _safe_float(metrics.get('avg_loss'))

        # رعایت ترتیب ستون‌ها بر اساس چیدمان RTL
        summary_data = [
            [reshape_fa("شاخص عمومی"), reshape_fa("ارزش"), reshape_fa("شاخص ریسک/بازده"), reshape_fa("ارزش")],
            [reshape_fa("تعداد کل معاملات"), reshape_fa(f"{total_trades}"), reshape_fa("Sharpe Ratio (نسبت شارپ)"), reshape_fa(f"{sharpe:.2f}")],
            [reshape_fa("وین ریت (Win Rate)"), reshape_fa(f"{win_rate:.1f}%"), reshape_fa("Profit Factor (ضریب سود)"), reshape_fa(f"{profit_factor:.2f}")],
            [reshape_fa("خالص سود/زیان"), reshape_fa(f"${total_profit:.2f}"), reshape_fa("Payoff Ratio (سود به زیان)"), reshape_fa(f"{payoff:.2f}")],
            [reshape_fa("بیشترین افت (Max DD)"), reshape_fa(f"${max_dd:.2f}"), reshape_fa("Expectancy (امید ریاضی)"), reshape_fa(f"${expectancy:.2f}")],
            [reshape_fa("میانگین سود"), reshape_fa(f"${avg_win:.2f}"), reshape_fa("میانگین زیان"), reshape_fa(f"${avg_loss:.2f}")]
        ]

        t_summary = Table(summary_data, colWidths=[140, 95, 140, 95])
        t_summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D47A1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F8FAFC')),
        ]))

        story.append(t_summary)
        story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۳. تفکیک عملکرد بر اساس نماد
        # ------------------------------------------------------------------
        symbol_data = calculate_symbol_breakdown(trades) if trades else []
        if symbol_data:
            sym_title = reshape_fa("📊 تفکیک عملکرد بر اساس نماد (Symbol Breakdown):")
            story.append(Paragraph(f"<b>{sym_title}</b>", section_title_style))
            story.append(Spacer(1, 4))

            # ترتیب ستون‌ها: نماد | تعداد معامله | وین ریت | سود/زیان کل (RTL)
            table_data = [[
                reshape_fa("نماد"),
                reshape_fa("تعداد معامله"),
                reshape_fa("وین ریت"),
                reshape_fa("سود/زیان کل")
            ]]

            for item in symbol_data:
                profit_val = _safe_float(item.get('profit'))
                profit_str = f"${profit_val:.2f}" if profit_val >= 0 else f"-${abs(profit_val):.2f}"
                win_r = _safe_float(item.get('win_rate'))

                table_data.append([
                    reshape_fa(str(item.get('symbol', ''))),
                    reshape_fa(str(item.get('count', 0))),
                    reshape_fa(f"{win_r:.1f}%"),
                    reshape_fa(profit_str)
                ])

            t_sym = Table(table_data, colWidths=[125, 115, 115, 115])
            t_sym.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#37474F')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CFD8DC')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FAFAFA')),
            ]))

            story.append(t_sym)
            story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۴. اضافه کردن نمودارهای Equity و Win Rate
        # ------------------------------------------------------------------
        charts = generate_report_charts(trades) if trades else {}

        if charts.get('equity'):
            chart_elements = [
                Paragraph(f"<b>{reshape_fa('📈 منحنی رشد حساب (Equity Curve) و افت حساب:')}</b>", section_title_style),
                Spacer(1, 4),
                Image(charts['equity'], width=470, height=200),
                Spacer(1, 10)
            ]
            story.append(KeepTogether(chart_elements))

        if charts.get('win_rate_symbol'):
            chart_elements2 = [
                Paragraph(f"<b>{reshape_fa('📊 مقایسه نرخ موفقیت (Win Rate) جفت‌ارزها:')}</b>", section_title_style),
                Spacer(1, 4),
                Image(charts['win_rate_symbol'], width=470, height=130),
                Spacer(1, 10)
            ]
            story.append(KeepTogether(chart_elements2))

        # ------------------------------------------------------------------
        # ۵. تحلیل Gemini (بخش حساس به متون فارسی)
        # ------------------------------------------------------------------
        if ai_analysis:
            ai_title = reshape_fa("🤖 تحلیل و ارزیابی هوش مصنوعی (Gemini):")
            story.append(Paragraph(f"<b>{ai_title}</b>", section_title_style))
            story.append(Spacer(1, 4))

            formatted_ai_text = reshape_multiline_fa(ai_analysis)
            story.append(Paragraph(formatted_ai_text, body_style))
            story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۶. جدول ریز معاملات (حداکثر ۲۵ معامله اخیر)
        # ------------------------------------------------------------------
        if trades:
            trades_title = reshape_fa("📋 ریز ۲۵ معامله اخیر:")
            story.append(Paragraph(f"<b>{trades_title}</b>", section_title_style))
            story.append(Spacer(1, 4))

            # ترتیب ستون‌ها: تاریخ | نماد | نوع | حجم | سود/زیان (RTL)
            table_trades_data = [
                [reshape_fa("تاریخ"), reshape_fa("نماد"), reshape_fa("نوع"), reshape_fa("حجم"), reshape_fa("سود/زیان")]
            ]

            sorted_trades = sorted(trades, key=lambda x: str(x.get('time', '')), reverse=True)[:25]

            for t in sorted_trades:
                profit_val = _safe_float(t.get('profit'))
                profit_str = f"${profit_val:.2f}" if profit_val >= 0 else f"-${abs(profit_val):.2f}"

                table_trades_data.append([
                    reshape_fa(str(t.get('time', ''))),
                    reshape_fa(str(t.get('symbol', ''))),
                    reshape_fa(str(t.get('type', ''))),
                    reshape_fa(str(t.get('volume', 0.0))),
                    reshape_fa(profit_str)
                ])

            t_trades = Table(table_trades_data, colWidths=[140, 90, 50, 50, 80])
            t_trades.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#263238')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E0E0E0')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FFFFFF')),
            ]))

            story.append(t_trades)

        # ساخت نهایی فایل PDF
        doc.build(story)

    finally:
        # بستن مطمئن و ایمن تمام بافرهای تصویری
        if isinstance(charts, dict):
            for img_buf in charts.values():
                if isinstance(img_buf, io.BytesIO) and not img_buf.closed:
                    img_buf.close()