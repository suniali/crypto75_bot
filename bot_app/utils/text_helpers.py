# utils/text_helpers.py
import arabic_reshaper
from bidi.algorithm import get_display


def reshape_fa(text: str) -> str:
    if not text:
        return ""
    reshaped_text = arabic_reshaper.reshape(str(text))
    return get_display(reshaped_text)

def reshape_multiline_fa(text: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    reshaped_lines = [reshape_fa(line) for line in lines]
    return "<br/>".join(reshaped_lines)


def fix_telegram_rtl(text: str) -> str:
    """اضافه کردن کاراکتر LTR/RTL Mark برای نمایش درست متون ترکیبی در تلگرام"""
    if not text:
        return ""
    return "\u200f" + str(text).replace("\n", "\n\u200f")