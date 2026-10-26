import io
from datetime import date

from PIL import Image, ImageFont

from services import og_card


def test_fit_line_breaks_on_words_and_adds_ellipsis() -> None:
    font = ImageFont.truetype(str(og_card.FONT_DIR / "Pretendard-SemiBold.otf"), 34)
    line = og_card.fit_line(
        "OpenAI 가 새로운 모델을 공개했다 그리고 많은 사람들이 놀랐다", font, 300
    )
    assert line.endswith("…")
    assert font.getlength(line) <= 300
    long_word = og_card.fit_line("가" * 80, font, 300)
    assert long_word.endswith("…") and font.getlength(long_word) <= 300
    assert og_card.fit_line("짧은 제목", font, 300) == "짧은 제목"


def test_render_day_card_size() -> None:
    png = og_card.render_day_card(date(2026, 10, 26), ["제목 하나", "제목 둘"], 2)
    assert Image.open(io.BytesIO(png)).size == (1200, 630)
