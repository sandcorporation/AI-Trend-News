"""날짜 뉴스 공유 카드 이미지(1200×630 PNG, #96): Pillow로 합성한다.

색·워드마크는 기본 og 이미지(scripts/og-image/og-default.svg)와 같다. 글꼴 Pretendard(OFL)는
`core/fonts/`에 동봉한다. 줄바꿈은 단어 단위이고 넘치면 「…」로 줄인다.
"""

import io
from datetime import date
from pathlib import Path

from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 630
CANVAS = "#ffffff"
INK = "#0b0d12"
TEXT = "#1f2430"
ACCENT = "#1e3a8a"
MARGIN = 96
MAX_TITLES = 4
WEEKDAYS = "월화수목금토일"
FONT_DIR = Path(settings.BASE_DIR) / "core" / "fonts"
ELLIPSIS = "…"

_Font = ImageFont.FreeTypeFont


def _font(weight: str, size: int) -> _Font:
    return ImageFont.truetype(str(FONT_DIR / f"Pretendard-{weight}.otf"), size)


def _width(font: _Font, text: str) -> float:
    return font.getlength(text)


def fit_line(text: str, font: _Font, max_width: float) -> str:
    """한 줄에 맞게 단어 단위로 자르고 잘렸으면 「…」를 붙인다. 첫 단어가 넘치면 글자 단위."""
    text = " ".join(text.split())
    if _width(font, text) <= max_width:
        return text
    words = text.split(" ")
    kept = ""
    for word in words:
        trial = f"{kept} {word}".strip()
        if _width(font, trial + ELLIPSIS) > max_width:
            break
        kept = trial
    if not kept:  # 첫 단어부터 넘친다
        for ch in text:
            if _width(font, kept + ch + ELLIPSIS) > max_width:
                break
            kept += ch
    return kept.rstrip() + ELLIPSIS


def render_day_card(day: date, titles: list[str], total: int) -> bytes:
    """그날의 카드 PNG. `titles`는 위에서부터 보여 줄 제목들(최대 4개만 쓴다)."""
    image = Image.new("RGB", (WIDTH, HEIGHT), CANVAS)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - MARGIN * 2

    draw.text((MARGIN, 70), f"AI · IT NEWS · {day.year}", font=_font("SemiBold", 26), fill=ACCENT)
    weekday = WEEKDAYS[day.weekday()]
    draw.text(
        (MARGIN - 4, 112),
        f"{day.month}월 {day.day}일 ({weekday})",
        font=_font("Bold", 112),
        fill=INK,
    )
    draw.text((MARGIN, 250), f"AI·IT 뉴스 {total}건", font=_font("SemiBold", 36), fill=ACCENT)

    title_font = _font("SemiBold", 34)
    bullet_w = _width(title_font, "· ")
    y = 322
    for title in titles[:MAX_TITLES]:
        draw.text((MARGIN, y), "·", font=title_font, fill=ACCENT)
        line = fit_line(title, title_font, inner - bullet_w)
        draw.text((MARGIN + bullet_w, y), line, font=title_font, fill=TEXT)
        y += 52

    draw.text((MARGIN - 2, 540), "Foresight", font=_font("Bold", 46), fill=INK)
    url_font = _font("SemiBold", 30)
    url = "news.sandcorp.co.kr"
    draw.text((WIDTH - MARGIN - _width(url_font, url), 552), url, font=url_font, fill=ACCENT)
    draw.rectangle((0, 606, WIDTH, HEIGHT), fill=ACCENT)

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
