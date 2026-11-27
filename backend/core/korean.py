"""한국어 조사 고르기."""

_HANGUL_FIRST, _HANGUL_LAST = 0xAC00, 0xD7A3
_RIEUL = 8  # 종성 번호
# 숫자·영문은 읽는 이름의 받침을 따른다(영·일·삼·육·칠·팔 / 엘·엠·엔·알).
_RIEUL_ENDINGS = "178lr"
_OTHER_BATCHIM_ENDINGS = "036mn"


def _final(char: str) -> int:
    """끝 글자의 종성 번호. 받침이 없으면 0이다."""
    if _HANGUL_FIRST <= ord(char) <= _HANGUL_LAST:
        return (ord(char) - _HANGUL_FIRST) % 28
    lower = char.lower()
    if lower in _RIEUL_ENDINGS:
        return _RIEUL
    return 1 if lower in _OTHER_BATCHIM_ENDINGS else 0


def josa(word: str, pair: str) -> str:
    """`word` 뒤에 맞는 조사를 붙인다. `pair`는 「받침 있을 때/없을 때」(예 `"을/를"`).

    끝의 괄호·따옴표 같은 기호는 건너뛰고 그 앞 글자를 본다. 「으로/로」는 ㄹ 받침에 「로」.
    """
    with_batchim, without = pair.split("/")
    last = next((c for c in reversed(word) if c.isalnum()), "")
    final = _final(last) if last else 0
    if final == _RIEUL and with_batchim == "으로":
        final = 0
    return word + (with_batchim if final else without)
