import pytest

from core.korean import josa


@pytest.mark.parametrize(
    ("word", "pair", "expected"),
    [
        ("이름", "을/를", "이름을"),  # 받침 있음
        ("연락처", "을/를", "연락처를"),  # 받침 없음
        ("의미", "은/는", "의미는"),
        ("배경", "은/는", "배경은"),
        ("URL", "을/를", "URL을"),  # 영문 끝
        ("API", "은/는", "API는"),
        ("항목 3", "이/가", "항목 3이"),  # 숫자 끝
        ("항목 2", "이/가", "항목 2가"),
        ("내용(선택)", "을/를", "내용(선택)을"),
        ("서울", "으로/로", "서울로"),
        ("항목 1", "으로/로", "항목 1로"),
        ("부산", "으로/로", "부산으로"),
    ],
)
def test_josa(word: str, pair: str, expected: str) -> None:
    assert josa(word, pair) == expected
