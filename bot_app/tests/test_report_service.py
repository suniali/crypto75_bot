import os
import io
import pytest
from unittest.mock import patch, MagicMock

# ساخت یا فراخوانی فیکسچرهای تست
from bot_app.services.report_service import generate_pdf_report, _safe_float


@pytest.fixture
def sample_trades():
    """دیتا نمونه برای معاملات جهت استفاده در تست‌ها"""
    return [
        {'time': '2026-09-01 10:00', 'symbol': 'EURUSD', 'type': 'BUY', 'volume': 0.1, 'profit': 150.0},
        {'time': '2026-09-01 12:00', 'symbol': 'GBPUSD', 'type': 'SELL', 'volume': 0.2, 'profit': -50.0},
        {'time': '2026-09-02 14:00', 'symbol': 'EURUSD', 'type': 'SELL', 'volume': 0.15, 'profit': 80.0},
        {'time': '2026-09-03 16:00', 'symbol': 'XAUUSD', 'type': 'BUY', 'volume': 0.05, 'profit': -120.0},
    ]


@pytest.fixture
def sample_ai_analysis():
    return "عملکرد معامله‌گر در این دوره مثبت بوده اما مدیریت ریسک در طلا نیازمند بهبود است."


# ------------------------------------------------------------------
# ۱. تست‌های تابع کمکی _safe_float
# ------------------------------------------------------------------
class TestSafeFloat:
    def test_safe_float_valid_inputs(self):
        assert _safe_float(10.5) == 10.5
        assert _safe_float("123.45") == 123.45
        assert _safe_float(0) == 0.0

    def test_safe_float_invalid_and_none_inputs(self):
        assert _safe_float(None) == 0.0
        assert _safe_float("invalid_string") == 0.0
        assert _safe_float(None, default=1.0) == 1.0


# ------------------------------------------------------------------
# ۲. تست‌های تابع generate_pdf_report
# ------------------------------------------------------------------
class TestGeneratePdfReport:

    @patch('bot_app.services.report_service.generate_report_charts')
    def test_generate_pdf_report_success(self, mock_generate_charts, tmp_path, sample_trades, sample_ai_analysis):
        """تست تولید موفقیت‌آمیز فایل PDF همراه با دیتا و نمودارها"""
        # تنظیم Mock برای نمودارها با استفاده از بافر واقعی io.BytesIO
        fake_img_bytes1 = io.BytesIO(b"fake_image_data_1")
        fake_img_bytes2 = io.BytesIO(b"fake_image_data_2")

        # ساخت یک تصویر واقعی خیلی کوچک مینیاتوری با PIL اگر لازم باشد یا مینی‌مال ReportLab Image Mock
        # جهت سادگی ReportLab نیاز به تصویر معتبر دارد؛ پس یک تصویر ساده با BytesIO شبیه‌سازی می‌کنیم:
        from PIL import Image as PILImage

        img_buf1 = io.BytesIO()
        img_buf2 = io.BytesIO()
        PILImage.new('RGB', (100, 100), color='red').save(img_buf1, format='PNG')
        PILImage.new('RGB', (100, 100), color='blue').save(img_buf2, format='PNG')
        img_buf1.seek(0)
        img_buf2.seek(0)

        mock_generate_charts.return_value = {
            'equity': img_buf1,
            'win_rate_symbol': img_buf2
        }

        output_pdf_path = str(tmp_path / "test_report.pdf")

        # اجرای تابع اصلی
        generate_pdf_report(
            filename=output_pdf_path,
            period_name="هفتگی",
            trades=sample_trades,
            ai_analysis=sample_ai_analysis
        )

        # بررسی وجود فایل و داشتن حجم بیشتر از ۰
        assert os.path.exists(output_pdf_path)
        assert os.path.getsize(output_pdf_path) > 0

    @patch('bot_app.services.report_service.generate_report_charts')
    def test_generate_pdf_report_empty_trades_and_no_ai(self, mock_generate_charts, tmp_path):
        """تست تولید PDF زمانی که لیست معاملات و تحلیل هوش مصنوعی خالی است (Edge Case)"""
        mock_generate_charts.return_value = {}
        output_pdf_path = str(tmp_path / "empty_report.pdf")

        generate_pdf_report(
            filename=output_pdf_path,
            period_name="روزانه",
            trades=[],
            ai_analysis=""
        )

        assert os.path.exists(output_pdf_path)
        assert os.path.getsize(output_pdf_path) > 0

    @patch('bot_app.services.report_service.generate_report_charts')
    def test_bytesio_buffers_closed_properly(self, mock_generate_charts, tmp_path, sample_trades):
        """تست بسته‌شدن صحیح بافرهای BytesIO جهت جلوگیری از Memory Leak"""
        buf1 = io.BytesIO()
        buf2 = io.BytesIO()

        # ساخت فایل تصویر موقت جهت تست
        from PIL import Image as PILImage
        PILImage.new('RGB', (50, 50)).save(buf1, format='PNG')
        PILImage.new('RGB', (50, 50)).save(buf2, format='PNG')
        buf1.seek(0)
        buf2.seek(0)

        mock_generate_charts.return_value = {
            'equity': buf1,
            'win_rate_symbol': buf2
        }

        output_pdf_path = str(tmp_path / "leak_test.pdf")

        generate_pdf_report(
            filename=output_pdf_path,
            period_name="ماهانه",
            trades=sample_trades,
            ai_analysis="تست پاکسازی حافظه"
        )

        # اطمینان از بسته شدن بافرها پس از اجرای تابع (در بلوک finally)
        assert buf1.closed is True
        assert buf2.closed is True