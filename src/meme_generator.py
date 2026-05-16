import io
import os
import re
import textwrap
from PIL import Image, ImageDraw, ImageFont

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FONT_PATH = os.path.join(_BASE_DIR, "Impact.ttf")


def _try_short_sentence(model, max_chars: int = 80, tries: int = 100) -> str | None:
    import re
    _DIGITS_RE = re.compile(r'\b\d{5,}\b')
    for _ in range(tries):
        candidate = model.generate(max_words=12, max_attempts=1, min_words=2)
        if not candidate:
            continue
        candidate = _DIGITS_RE.sub('', candidate).strip()
        candidate = re.sub(r'\s+', ' ', candidate).strip()
        if candidate and len(candidate) <= max_chars:
            return candidate
    return None


def render_meme(image_bytes: bytes, caption: str) -> bytes:
    base = Image.open(io.BytesIO(image_bytes))
    if hasattr(base, "n_frames") and base.n_frames > 1:
        base.seek(0)
    base = base.convert("RGBA")
    img_w, img_h = base.size

    text_upper = caption.upper()
    import unicodedata
    text_upper = unicodedata.normalize('NFKD', text_upper)
    text_upper = ''.join(c for c in text_upper if not unicodedata.combining(c))
    text_upper = re.sub(r'\s+', ' ', text_upper).strip()
    padding_h = int(img_w * 0.05)
    usable_w = img_w - 2 * padding_h

    font_size = max(24, img_w // 12)
    font = None
    lines: list[str] = []

    while font_size >= 16:
        f = ImageFont.truetype(_FONT_PATH, font_size)
        avg_char_w = max(1, f.getlength("A"))
        chars_per_line = max(1, int(usable_w / avg_char_w))
        wrapped = textwrap.wrap(text_upper, width=chars_per_line) or [text_upper[:max(1, chars_per_line)]]
        if all(f.getlength(line) <= usable_w for line in wrapped):
            font = f
            lines = wrapped
            break
        font_size -= 2

    if font is None:
        font_size = 16
        font = ImageFont.truetype(_FONT_PATH, font_size)
        avg_char_w = max(1, font.getlength("A"))
        chars_per_line = max(1, int(usable_w / avg_char_w))
        lines = textwrap.wrap(text_upper, width=chars_per_line) or [text_upper[:max(1, chars_per_line)]]

    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    padding_v = int(img_w * 0.08)
    min_banner_h = int(img_h * 0.12)
    if len(lines) * line_h + 2 * padding_v < min_banner_h:
        padding_v = (min_banner_h - len(lines) * line_h) // 2
    banner_h = len(lines) * line_h + 2 * padding_v

    out = Image.new("RGB", (img_w, banner_h + img_h), "white")

    white_bg = Image.new("RGBA", base.size, (255, 255, 255, 255))
    white_bg.alpha_composite(base)
    out.paste(white_bg.convert("RGB"), (0, banner_h))

    draw = ImageDraw.Draw(out)
    y = padding_v
    for line in lines:
        line_w = font.getlength(line)
        x = int((img_w - line_w) / 2)
        draw.text((x, y), line, fill=(0, 0, 0), font=font)
        y += line_h

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()
