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


def generate_pdf_report(filename: str, period_name: str, trades: list, ai_analysis: str):
    """تولید گزارش PDF استاندارد و شکیل با پشتیبانی ۱۰۰٪ از متون فارسی و نمودارها"""

    # تنظیمات صفحه PDF
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25
    )
    story = []

    # استایل‌ها
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
        alignment=2,
        textColor=colors.HexColor("#1A237E")
    )

    # ------------------------------------------------------------------
    # ۱. عنوان اصلی
    # ------------------------------------------------------------------
    title_text = reshape_fa(f"گزارش جامع و تخصصی عملکرد معاملات ({period_name})")
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Spacer(1, 10))

    # ------------------------------------------------------------------
    # ۲. خلاصه آمار و شاخص‌های وال‌استریت
    # ------------------------------------------------------------------
    metrics = calculate_trading_metrics(trades)
    total_trades = len(trades)
    total_profit = sum(t.get('profit', 0.0) for t in trades)
    win_trades = sum(1 for t in trades if t.get('profit', 0.0) > 0)
    win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0.0

    # تمامی سلول‌های متنی حتماً باید از reshape_fa عبور کنند
    summary_data = [
        [reshape_fa("ارزش"), reshape_fa("شاخص ریسک/بازده"), reshape_fa("ارزش"), reshape_fa("شاخص عمومی")],
        [reshape_fa(f"{total_trades}"), reshape_fa("تعداد کل معاملات"), reshape_fa(f"{metrics.get('sharpe_ratio', 0.0):.2f}"), reshape_fa("Sharpe Ratio (نسبت شارپ)")],
        [reshape_fa(f"{win_rate:.1f}%"), reshape_fa("وین ریت (Win Rate)"), reshape_fa(f"{metrics.get('profit_factor', 0.0):.2f}"), reshape_fa("Profit Factor (ضریب سود)")],
        [reshape_fa(f"${total_profit:.2f}"), reshape_fa("خالص سود/زیان"), reshape_fa(f"{metrics.get('payoff_ratio', 0.0):.2f}"), reshape_fa("Payoff Ratio (سود به زیان)")],
        [reshape_fa(f"${metrics.get('max_drawdown', 0.0):.2f}"), reshape_fa("بیشترین افت (Max DD)"), reshape_fa(f"${metrics.get('expectancy', 0.0):.2f}"), reshape_fa("Expectancy (امید ریاضی)")],
        [reshape_fa(f"${metrics.get('avg_win', 0.0):.2f}"), reshape_fa("میانگین سود"), reshape_fa(f"${metrics.get('avg_loss', 0.0):.2f}"), reshape_fa("میانگین زیان")]
    ]

    t_summary = Table(summary_data, colWidths=[95, 140, 95, 140])
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
    symbol_data = calculate_symbol_breakdown(trades)
    if symbol_data:
        sym_title = reshape_fa("📊 تفکیک عملکرد بر اساس نماد (Symbol Breakdown):")
        story.append(Paragraph(f"<b>{sym_title}</b>", section_title_style))
        story.append(Spacer(1, 4))

        table_data = [[
            reshape_fa("سود/زیان کل"),
            reshape_fa("وین ریت"),
            reshape_fa("تعداد معامله"),
            reshape_fa("نماد")
        ]]

        for item in symbol_data:
            profit_val = item['profit']
            profit_str = f"${profit_val:.2f}" if profit_val >= 0 else f"-${abs(profit_val):.2f}"
            table_data.append([
                reshape_fa(profit_str),
                reshape_fa(f"{item['win_rate']:.1f}%"),
                reshape_fa(str(item['count'])),
                reshape_fa(str(item['symbol']))
            ])

        t_sym = Table(table_data, colWidths=[115, 115, 115, 125])
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
    charts = generate_report_charts(trades)

    if 'equity' in charts and charts['equity']:
        chart_elements = []
        chart_elements.append(
            Paragraph(f"<b>{reshape_fa('📈 منحنی رشد حساب (Equity Curve) و افت حساب:')}</b>", section_title_style))
        chart_elements.append(Spacer(1, 4))
        chart_elements.append(Image(charts['equity'], width=470, height=200))
        chart_elements.append(Spacer(1, 10))
        story.append(KeepTogether(chart_elements))

    if 'win_rate_symbol' in charts and charts['win_rate_symbol']:
        chart_elements2 = []
        chart_elements2.append(
            Paragraph(f"<b>{reshape_fa('📊 مقایسه نرخ موفقیت (Win Rate) جفت‌ارزها:')}</b>", section_title_style))
        chart_elements2.append(Spacer(1, 4))
        chart_elements2.append(Image(charts['win_rate_symbol'], width=470, height=130))
        chart_elements2.append(Spacer(1, 10))
        story.append(KeepTogether(chart_elements2))

    # ------------------------------------------------------------------
    # ۵. تحلیل Gemini (بخش حساس به متون فارسی)
    # ------------------------------------------------------------------
    if ai_analysis:
        ai_elements = []
        ai_title = reshape_fa("🤖 تحلیل و ارزیابی هوش مصنوعی (Gemini):")
        ai_elements.append(Paragraph(f"<b>{ai_title}</b>", section_title_style))
        ai_elements.append(Spacer(1, 4))

        # اصلاح متون چندخطی
        formatted_ai_text = reshape_multiline_fa(ai_analysis)
        ai_elements.append(Paragraph(formatted_ai_text, body_style))
        ai_elements.append(Spacer(1, 10))

        story.append(KeepTogether(ai_elements))

    # ------------------------------------------------------------------
    # ۶. جدول ریز معاملات (حداکثر ۲۵ معامله اخیر)
    # ------------------------------------------------------------------
    if trades:
        trades_elements = []
        trades_title = reshape_fa("📋 ریز ۲۵ معامله اخیر:")
        trades_elements.append(Paragraph(f"<b>{trades_title}</b>", section_title_style))
        trades_elements.append(Spacer(1, 4))

        table_trades_data = [
            [reshape_fa("سود/زیان"), reshape_fa("حجم"), reshape_fa("نوع"), reshape_fa("نماد"), reshape_fa("تاریخ")]
        ]

        sorted_trades = sorted(trades, key=lambda x: str(x.get('time', '')), reverse=True)[:25]

        for t in sorted_trades:
            profit_val = t.get('profit', 0.0)
            profit_str = f"${profit_val:.2f}" if profit_val >= 0 else f"-${abs(profit_val):.2f}"

            table_trades_data.append([
                reshape_fa(profit_str),
                reshape_fa(str(t.get('volume', 0.0))),
                reshape_fa(str(t.get('type', ''))),
                reshape_fa(str(t.get('symbol', ''))),
                reshape_fa(str(t.get('time', '')))
            ])

        t_trades = Table(table_trades_data, colWidths=[80, 50, 50, 90, 140])
        t_trades.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#263238')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E0E0E0')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FFFFFF')),
        ]))

        trades_elements.append(t_trades)
        story.append(KeepTogether(trades_elements))

    # ساخت فایل PDF
    try:
        doc.build(story)
    finally:
        for img_buf in charts.values():
            if isinstance(img_buf, io.BytesIO):
                img_buf.close()