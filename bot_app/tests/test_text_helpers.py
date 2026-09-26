import pytest
from bot_app.utils.text_helpers import (
    reshape_fa,
    reshape_multiline_fa,
    fix_telegram_rtl
)


class TestReshapeFa:

    def test_reshape_fa_empty_and_none_inputs(self):
        """تست عملکرد ورودی‌های خالی و None"""
        assert reshape_fa("") == ""
        assert reshape_fa(None) == ""

    def test_reshape_fa_persian_text(self):
        """تست اصلاح چسبندگی و جهت حروف فارسی"""
        raw_text = "گزارش عملکرد"
        reshaped = reshape_fa(raw_text)

        assert isinstance(reshaped, str)
        assert len(reshaped) > 0
        # متن تغییر شکل‌یافته نباید دقیقاً برابر متن خام ورودی باشد (حروف به هم متصل و معکوس جهت رندر شده‌اند)
        assert reshaped != raw_text

    def test_reshape_fa_numeric_and_mixed_inputs(self):
        """تست ورودی‌های عددی و متون ترکیبی فارسی/انگلیسی"""
        assert reshape_fa(12345) != ""

        mixed_text = "وین ریت (Win Rate): 65.5%"
        reshaped = reshape_fa(mixed_text)
        assert isinstance(reshaped, str)


class TestReshapeMultilineFa:

    def test_reshape_multiline_fa_empty_and_none_inputs(self):
        """تست ورودی‌های خالی برای متن چندخطی"""
        assert reshape_multiline_fa("") == ""
        assert reshape_multiline_fa(None) == ""

    def test_reshape_multiline_fa_conversion(self):
        """تست تبدیل نیولاین‌ها (\n) به تگ <br/> جهت نمایش در ReportLab"""
        multiline_text = "سطر اول\nسطر دوم\nسطر سوم"
        reshaped_multiline = reshape_multiline_fa(multiline_text)

        # باید کاراکترهای \n به تگ HTML شکاف سطر <br/> تبدیل شده باشند
        assert "<br/>" in reshaped_multiline
        assert len(reshaped_multiline.split("<br/>")) == 3


class TestFixTelegramRtl:

    def test_fix_telegram_rtl_empty_and_none_inputs(self):
        """تست ورودی‌های خالی برای تابع تلگرام"""
        assert fix_telegram_rtl("") == ""
        assert fix_telegram_rtl(None) == ""

    def test_fix_telegram_rtl_adds_rlm_character(self):
        """تست اضافه شدن کاراکتر RLM (\u200f) به ابتدای متن و خطوط جدید"""
        raw_text = "سلام\nتست چیدمان RTL"
        fixed = fix_telegram_rtl(raw_text)

        # باید با کاراکتر Unicode \u200f شروع شود
        assert fixed.startswith("\u200f")

        # تمام خطوط جدید هم باید حاوی این کاراکتر کنترل جهت باشند
        assert "\n\u200f" in fixed
        assert fixed.count("\u200f") == 2