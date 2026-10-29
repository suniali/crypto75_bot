import os
import io

from decouple import config
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
from reportlab.pdfgen import canvas

from bot_app.services.analysis_service import generate_report_charts
from bot_app.utils.math_helpers import calculate_symbol_breakdown
from bot_app.utils.text_helpers import reshape_fa, reshape_multiline_fa

# ------------------------------------------------------------------
# ثبت فونت وزیر به همراه مدیریت مسیر چندلایه
# ------------------------------------------------------------------
CURENCY_UNIT=config("CURENCY_UNIT", default="$")
 
SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(SERVICES_DIR))
FONT_PATH = os.path.join(ROOT_DIR, "Vazirmatn-Regular.ttf")

if os.path.exists(FONT_PATH):
    pdfmetrics.registerFont(TTFont('Vazir', FONT_PATH))
    print(f"✅ Font successfully loaded: {FONT_PATH}")
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


class NumberedCanvas(canvas.Canvas):
    """کانواس سفارشی جهت اضافه کردن هدر و فوتر به همراه شماره صفحه متحرک (صفحه X از Y)"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#757575"))
        
        # خط جداسازی فوتر
        self.setStrokeColor(colors.HexColor("#E0E0E0"))
        self.setLineWidth(0.5)
        self.line(25, 35, 587, 35)

        # فوتر: سمت راست نام سیستم و سمت چپ شماره صفحه
        footer_text = f"Page {self._pageNumber} of {page_count}"
        self.drawString(25, 22, "JARVIS JOURNAL 2027 - Automated Trading Report")
        self.drawRightString(587, 22, footer_text)
        self.restoreState()


def generate_pdf_report(filename: str, period_name: str, metrics:dict, ai_analysis: str):
    """تولید گزارش PDF جامع، هوشمند و شکیل بر اساس کل خروجی‌های calculate_trading_metrics"""

    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=25,
        leftMargin=25,
        topMargin=30,
        bottomMargin=45
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
        leading=13.5,
        alignment=2,  # راست‌چین
        textColor=colors.HexColor("#212121")
    )

    title_style = ParagraphStyle(
        'FaTitle',
        parent=styles['Heading1'],
        fontName=font_name,
        fontSize=13,
        leading=17,
        alignment=1,  # وسط‌چین
        textColor=colors.HexColor("#0D47A1")
    )

    section_title_style = ParagraphStyle(
        'FaSectionTitle',
        parent=styles['Heading2'],
        fontName=font_name,
        fontSize=9.5,
        leading=13,
        alignment=2,  # راست‌چین
        textColor=colors.HexColor("#1A237E")
    )

    try:
        # ------------------------------------------------------------------
        # ۱. فراخوانی یکپارچه و استخراج کامل متغیرها از calculate_trading_metrics
        # ------------------------------------------------------------------
        trades=metrics.get('trades', [])

        total_trades = metrics.get('total_trades', 0)
        win_rate = metrics.get('win_rate', 0.0)
        wins = metrics.get('wins', 0)
        losses = metrics.get('losses', 0)
        be_count = metrics.get('be_count', 0)
        total_profit = metrics.get('total_net_profit', 0.0)
        profit_factor = metrics.get('profit_factor', 0.0)
        max_dd = metrics.get('max_drawdown', 0.0)
        payoff = metrics.get('payoff_ratio', 0.0)
        expectancy = metrics.get('expectancy', 0.0)
        sharpe = metrics.get('sharpe_ratio', 0.0)
        avg_win = metrics.get('avg_win', 0.0)
        avg_loss = metrics.get('avg_loss', 0.0)
        total_comm = metrics.get('total_commission', 0.0)
        total_swap = metrics.get('total_swap', 0.0)
        
        stability_label = metrics.get('stability_label', 'نامشخص')
        risk_level_label = metrics.get('risk_level_label', 'نامشخص')
        mental_discipline_score = metrics.get('mental_discipline_score', 50)
        discipline_label = metrics.get('discipline_label', 'نرمال')

        streaks = metrics.get('streaks', {})
        max_win_days = streaks.get('max_win_streak_days', 0) if isinstance(streaks, dict) else 0
        max_win_trades = streaks.get('max_win_streak_trades', 0) if isinstance(streaks, dict) else 0

        # ------------------------------------------------------------------
        # ۲. عنوان اصلی گزارش
        # ------------------------------------------------------------------
        title_text = reshape_fa(f"گزارش جامع و تخصصی عملکرد معاملات ({period_name})")
        story.append(Paragraph(f"<b>{title_text}</b>", title_style))
        story.append(Spacer(1, 8))

        # ------------------------------------------------------------------
        # ۳. جدول اصلی: خلاصه آمار و شاخص‌های وال‌استریت (RTL)
        # ------------------------------------------------------------------
        summary_data = [
            [reshape_fa("شاخص عملکرد و حجم"), reshape_fa("مقدار"), reshape_fa("شاخص تحلیلی و مدیریت ریسک"), reshape_fa("مقدار")],
            [reshape_fa("تعداد کل معاملات (برد/باخت/BE)"), reshape_fa(f"{total_trades} ({wins}/{losses}/{be_count})"), reshape_fa("Sharpe Ratio (نسبت شارپ)"), reshape_fa(f"{sharpe:.2f}")],
            [reshape_fa("وین ریت (Win Rate)"), reshape_fa(f"{win_rate:.1f}%"), reshape_fa("Profit Factor (ضریب سودآوری)"), reshape_fa(f"{profit_factor:.2f}")],
            [reshape_fa("سود/زیان خالص کل"), reshape_fa(f"{CURENCY_UNIT}{total_profit:.2f}"), reshape_fa("Payoff Ratio (نسبت سود/زیان)"), reshape_fa(f"{payoff:.2f}")],
            [reshape_fa("بیشترین افت حساب (Max DD)"), reshape_fa(f"{CURENCY_UNIT}{max_dd:.2f}"), reshape_fa("Expectancy (امید ریاضی)"), reshape_fa(f"{expectancy:.2f}")],
            [reshape_fa("میانگین سود / میانگین زیان"), reshape_fa(f"{CURENCY_UNIT}{avg_win:.2f} / {CURENCY_UNIT}{avg_loss:.2f}"), reshape_fa("کمیسیون و سواپ کل"), reshape_fa(f"{CURENCY_UNIT}{total_comm:.2f} / {CURENCY_UNIT}{total_swap:.2f}")],
            [reshape_fa("ارزیابی ریسک حساب"), reshape_fa(f"{risk_level_label}"), reshape_fa("پایداری سیستم استراتژی"), reshape_fa(f"{stability_label}")],
            [reshape_fa("ضریب انضباط ذهنی (AI)"), reshape_fa(f"{mental_discipline_score}% ({discipline_label})"), reshape_fa("بیشترین تسلسل موفقیت"), reshape_fa(f"{max_win_days} روز ({max_win_trades} ترید)")]
        ]

        t_summary = Table(summary_data, colWidths=[150, 85, 150, 85])
        t_summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D47A1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#F8FAFC')),
        ]))

        story.append(t_summary)
        story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۴. تفکیک عملکرد بر اساس نماد (Symbol Breakdown)
        # ------------------------------------------------------------------
        symbol_data = calculate_symbol_breakdown(trades) if trades else []
        if symbol_data:
            sym_title = reshape_fa("📊 تفکیک عملکرد بر اساس نماد معاملاتی:")
            story.append(Paragraph(f"<b>{sym_title}</b>", section_title_style))
            story.append(Spacer(1, 3))

            table_data = [[
                reshape_fa("نماد"),
                reshape_fa("تعداد معامله"),
                reshape_fa("وین ریت"),
                reshape_fa("سود/زیان کل")
            ]]

            for item in symbol_data:
                profit_val = _safe_float(item.get('profit'))
                profit_str = f"{CURENCY_UNIT}{profit_val:.2f}" if profit_val >= 0 else f"-{CURENCY_UNIT}{abs(profit_val):.2f}"
                win_r = _safe_float(item.get('win_rate'))

                table_data.append([
                    reshape_fa(str(item.get('symbol', ''))),
                    reshape_fa(str(item.get('count', 0))),
                    reshape_fa(f"{win_r:.1f}%"),
                    reshape_fa(profit_str)
                ])

            t_sym = Table(table_data, colWidths=[120, 110, 120, 120])
            t_sym.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#37474F')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
                ('TOPPADDING', (0, 0), (-1, -1), 2.5),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CFD8DC')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FAFAFA')),
            ]))

            story.append(t_sym)
            story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۵. نمودارهای تصویری (Equity, Win Rate & PnL Distribution)
        # ------------------------------------------------------------------
        charts = generate_report_charts(trades) if trades else {}

        if charts.get('equity'):
            chart_elements = [
                Paragraph(f"<b>{reshape_fa('📈 منحنی رشد حساب (Equity Curve) و روند دراوداون:')}</b>", section_title_style),
                Spacer(1, 3),
                Image(charts['equity'], width=470, height=180),
                Spacer(1, 8)
            ]
            story.append(KeepTogether(chart_elements))

        if charts.get('win_rate_symbol'):
            chart_elements2 = [
                Paragraph(f"<b>{reshape_fa('📊 مقایسه نرخ موفقیت (Win Rate) نمادها:')}</b>", section_title_style),
                Spacer(1, 3),
                Image(charts['win_rate_symbol'], width=470, height=120),
                Spacer(1, 8)
            ]
            story.append(KeepTogether(chart_elements2))

        if charts.get('pnl_dist'):
            chart_elements3 = [
                Paragraph(f"<b>{reshape_fa('⚖️ توزیع سود و زیان معامله به معامله (PnL Distribution):')}</b>", section_title_style),
                Spacer(1, 3),
                Image(charts['pnl_dist'], width=470, height=140),
                Spacer(1, 8)
            ]
            story.append(KeepTogether(chart_elements3))

        # ------------------------------------------------------------------
        # ۶. گزارش تحلیلی هوش مصنوعی (Gemini Analysis)
        # ------------------------------------------------------------------
        if ai_analysis:
            ai_title = reshape_fa("🤖 ارزیابی عصب‌شناختی و روانشناسی هوش مصنوعی (Gemini Core 2027):")
            story.append(Paragraph(f"<b>{ai_title}</b>", section_title_style))
            story.append(Spacer(1, 3))

            formatted_ai_text = reshape_multiline_fa(ai_analysis)
            story.append(Paragraph(formatted_ai_text, body_style))
            story.append(Spacer(1, 10))

        # ------------------------------------------------------------------
        # ۷. جدول ریز معاملات (حداکثر ۲۵ معامله اخیر)
        # ------------------------------------------------------------------
        if trades:
            trades_title = reshape_fa("📋 ریز ۲۵ معامله اخیر ثبت‌شده در ژورنال:")
            story.append(Paragraph(f"<b>{trades_title}</b>", section_title_style))
            story.append(Spacer(1, 3))

            table_trades_data = [
                [reshape_fa("تاریخ"), reshape_fa("نماد"), reshape_fa("نوع"), reshape_fa("حجم"), reshape_fa("سود/زیان")]
            ]

            # مرتب‌سازی نزولی بر اساس زمان ثبت
            sorted_trades = sorted(
                trades,
                key=lambda x: getattr(x, 'entry_time', None) or getattr(x, 'created_at', '') or str(x.get('time', '')),
                reverse=True
            )[:25]

            for t in sorted_trades:
                # پشتیبانی از هر دو حالت Object و Dict
                if isinstance(t, dict):
                    t_time = str(t.get('time', ''))
                    t_sym = str(t.get('symbol', ''))
                    t_type = str(t.get('type', ''))
                    t_vol = str(t.get('volume', 0.0))
                    profit_val = _safe_float(t.get('profit'))
                else:
                    t_time = t.entry_time.strftime('%Y-%m-%d %H:%M') if getattr(t, 'entry_time', None) else ''
                    t_sym = str(getattr(t, 'symbol', ''))
                    t_type = str(getattr(t, 'trade_type', ''))
                    t_vol = str(getattr(t, 'volume', 0.0))
                    profit_val = _safe_float(getattr(t, 'net_profit', 0.0))

                profit_str = f"{CURENCY_UNIT}{profit_val:.2f}" if profit_val >= 0 else f"-{CURENCY_UNIT}{abs(profit_val):.2f}"

                table_trades_data.append([
                    reshape_fa(t_time),
                    reshape_fa(t_sym),
                    reshape_fa(t_type),
                    reshape_fa(t_vol),
                    reshape_fa(profit_str)
                ])

            t_trades = Table(table_trades_data, colWidths=[130, 85, 60, 65, 130])
            t_trades.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#263238')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTSIZE', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E0E0E0')),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#FFFFFF')),
            ]))

            story.append(t_trades)



        # ساخت فایل PDF با استفاده از کلاس NumberedCanvas
        doc.build(story, canvasmaker=NumberedCanvas)

    finally:
        # بستن بافرهای تصویری جهت جلوگیری از ایجاد Memory Leak
        if isinstance(charts, dict):
            for img_buf in charts.values():
                if isinstance(img_buf, io.BytesIO) and not img_buf.closed:
                    img_buf.close()