"""소스별 거르기 규칙(#158) 검증과 적용. 모든 비교는 대소문자 구분 부분 문자열이다."""

from collections.abc import Mapping

KEYS = ("exclude_url", "exclude_title", "include_any")
EXCERPT_HEAD = 300  # include_any 가 제목과 함께 보는 발췌 앞부분 길이


class FilterRulesError(ValueError):
    pass


def validate_filter_rules(rules: object) -> dict[str, list[str]]:
    """규칙을 검증해 키별 문자열 목록으로 돌려준다. 잘못되면 FilterRulesError."""
    if rules is None or rules == "":
        return {}
    if not isinstance(rules, Mapping):
        raise FilterRulesError("규칙은 JSON 객체여야 합니다.")
    unknown = set(rules) - set(KEYS)
    if unknown:
        raise FilterRulesError(f"알 수 없는 키: {', '.join(sorted(map(str, unknown)))}")
    result: dict[str, list[str]] = {}
    for key in KEYS:
        if key not in rules:
            continue
        value = rules[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise FilterRulesError(f"{key}는 문자열 목록이어야 합니다.")
        if any(not v for v in value):
            raise FilterRulesError(f"{key}에 빈 문자열은 쓸 수 없습니다.")
        if value:
            result[key] = list(value)
    return result


def filter_reason(rules: Mapping[str, list[str]], url: str, title: str, excerpt: str) -> str:
    """걸리면 사유 문자열, 통과면 빈 문자열."""
    for needle in rules.get("exclude_url", []):
        if needle in url:
            return f"exclude_url:{needle}"
    for needle in rules.get("exclude_title", []):
        if needle in title:
            return f"exclude_title:{needle}"
    include = rules.get("include_any", [])
    if include:
        haystack = f"{title}\n{excerpt[:EXCERPT_HEAD]}"
        if not any(n in haystack for n in include):
            return "include_any 없음"
    return ""
