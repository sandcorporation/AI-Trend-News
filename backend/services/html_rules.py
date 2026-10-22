"""HTML 소스의 선택자 규칙 검증(admin 폼과 어댑터가 같이 쓴다)."""

from collections.abc import Mapping

REQUIRED_KEYS = ("item", "link", "title", "published")
OPTIONAL_KEYS = ("excerpt",)


class HtmlRulesError(ValueError):
    pass


def validate_html_rules(rules: object) -> dict[str, str]:
    """규칙을 검증해 문자열 사전으로 돌려준다. 잘못되면 HtmlRulesError."""
    if not isinstance(rules, Mapping):
        raise HtmlRulesError("규칙은 JSON 객체여야 합니다.")
    unknown = set(rules) - set(REQUIRED_KEYS) - set(OPTIONAL_KEYS)
    if unknown:
        raise HtmlRulesError(f"알 수 없는 키: {', '.join(sorted(map(str, unknown)))}")
    missing = [k for k in REQUIRED_KEYS if not str(rules.get(k) or "").strip()]
    if missing:
        raise HtmlRulesError(f"필수 키가 없습니다: {', '.join(missing)}")
    result: dict[str, str] = {}
    for key in (*REQUIRED_KEYS, *OPTIONAL_KEYS):
        value = rules.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise HtmlRulesError(f"{key}는 문자열이어야 합니다.")
        result[key] = value.strip()
    return result
