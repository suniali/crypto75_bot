# تنظیمات کتابخانه‌های PDF فارسی
import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('Vazir', 'Vazirmatn-Regular.ttf'))


from analysis_service import (
    generate_equity_chart,
    calculate_trading_metrics,
    calculate_symbol_breakdown,
    generate_report_charts,
)


def reshape_fa(text: str) -> str:
    """اصلاح جهت متون فارسی برای ReportLab"""
    if not text:
        return ""
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)


def generate_pdf_report(filename: str, period_name: str, trades: list, ai_analysis: str):
    doc = SimpleDocTemplate(filename, pagesize=letter, rightMargin=25, leftMargin=25, topMargin=25, bottomMargin=25)
    story = []

    styles = getSampleStyleSheet()
    body_style = ParagraphStyle('FaStyle', parent=styles['Normal'], fontName='Vazir', fontSize=9, leading=14, alignment=2)
    title_style = ParagraphStyle('FaTitle', parent=styles['Heading1'], fontName='Vazir', fontSize=15, leading=20, alignment=1)

    # ۱. عنوان
    title_text = reshape_fa(f"گزارش جامع و تخصصی عملکرد معاملات ({period_name})")
    story.append(Paragraph(f"<b>{title_text}</b>", title_style))
    story.append(Spacer(1, 12))

    # ۲. خلاصه آمار و شاخص‌های وال‌استریت
    metrics = calculate_trading_metrics(trades)
    total_trades = len(trades)
    total_profit = sum(t['profit'] for t in trades)
    win_trades = sum(1 for t in trades if t['profit'] > 0)
    win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0

    summary_data = [
        [reshape_fa("ارزش"), reshape_fa("شاخص ریسک/بازده"), reshape_fa("ارزش"), reshape_fa("شاخص عمومی")],
        [f"{total_trades}", reshape_fa("تعداد کل معاملات"), f"{metrics['sharpe_ratio']}", reshape_fa("Sharpe Ratio (نسبت شارپ)")],
        [f"%{win_rate:.1f}", reshape_fa("وین ریت (Win Rate)"), f"{metrics['profit_factor']}", reshape_fa("Profit Factor (ضریب سود)")],
        [f"${total_profit:.2f}", reshape_fa("خالص سود/زیان"), f"{metrics['payoff_ratio']}", reshape_fa("Payoff Ratio (سود به زیان)")],
        [f"${metrics['max_drawdown']:.2f}", reshape_fa("بیشترین افت (Max DD)"), f"${metrics['expectancy']:.2f}", reshape_fa("Expectancy (امید ریاضی)")],
        [f"${metrics['avg_win']:.2f}", reshape_fa("میانگین سود"), f"${metrics['avg_loss']:.2f}", reshape_fa("میانگین زیان")]
    ]

    t_summary = Table(summary_data, colWidths=[95, 140, 95, 140])
    t_summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D47A1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Vazir'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F5F5F5')),
    ]))

    story.append(t_summary)
    story.append(Spacer(1, 12))

    # ۳. تفکیک عملکرد بر اساس نماد
    symbol_data = calculate_symbol_breakdown(trades)
    if symbol_data:
        sym_title = reshape_fa("📊 تفکیک عملکرد بر اساس نماد (Symbol Breakdown):")
        story.append(Paragraph(f"<b>{sym_title}</b>", body_style))
        story.append(Spacer(1, 6))

        table_data = [[
            reshape_fa("سود/زیان کل"),
            reshape_fa("وین ریت"),
            reshape_fa("تعداد معامله"),
            reshape_fa("نماد")
        ]]

        for item in symbol_data:
            profit_str = f"${item['profit']:.2f}" if item['profit'] >= 0 else f"-${abs(item['profit']):.2f}"
            table_data.append([
                profit_str,
                f"%{item['win_rate']}",
                str(item['count']),
                item['symbol']
            ])

        t_sym = Table(table_data, colWidths=[115, 115, 115, 125])
        t_sym.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#37474F')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), 'Vazir'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FAFAFA')),
        ]))

        story.append(t_sym)
        story.append(Spacer(1, 12))

    # ۴. اضافه کردن نمودارهای Equity و Win Rate
    charts = generate_report_charts(trades)
    if 'equity' in charts:
        story.append(Paragraph(f"<b>{reshape_fa('📈 منحنی رشد حساب (Equity Curve) و افت حساب:')}</b>", body_style))
        story.append(Spacer(1, 4))
        story.append(Image(charts['equity'], width=470, height=220))
        story.append(Spacer(1, 10))

    if 'win_rate_symbol' in charts:
        story.append(Paragraph(f"<b>{reshape_fa('📊 مقایسه نرخ موفقیت (Win Rate) جفت‌ارزها:')}</b>", body_style))
        story.append(Spacer(1, 4))
        story.append(Image(charts['win_rate_symbol'], width=470, height=150))
        story.append(Spacer(1, 10))

    # ۵. تحلیل Gemini
    ai_title = reshape_fa("🤖 تحلیل و ارزیابی هوش مصنوعی (Gemini):")
    story.append(Paragraph(f"<b>{ai_title}</b>", body_style))
    story.append(Spacer(1, 6))

    formatted_ai_text = reshape_fa(ai_analysis).replace('\n', '<br/>')
    story.append(Paragraph(formatted_ai_text, body_style))
    story.append(Spacer(1, 12))

    # ۶. جدول ریز معاملات
    table_trades_data = [
        [reshape_fa("سود/زیان"), reshape_fa("حجم"), reshape_fa("نوع"), reshape_fa("نماد"), reshape_fa("تاریخ")]
    ]
    for t in trades[:25]:
        table_trades_data.append([
            f"${t['profit']:.2f}",
            f"{t['volume']}",
            t['type'],
            t['symbol'],
            t['time']
        ])

    t_trades = Table(table_trades_data, colWidths=[80, 50, 50, 90, 140])
    t_trades.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#263238')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), 'Vazir'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
    ]))
    story.append(t_trades)

    doc.build(story)