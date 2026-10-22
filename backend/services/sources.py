"""소스 등록·수정 규칙(#9). admin 폼과 같은 검증(`Source.clean`)을 서비스에서도 건다.

오류는 `SourceError`의 하위 클래스이고 메시지를 MCP·관리자 API가 그대로 돌려준다.
"""

from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.db import transaction

from collection.models import Source


class SourceError(Exception):
    """소스 규칙 위반의 공통 부모."""


class SourceNotFound(SourceError):
    pass


class SourceInvalid(SourceError):
    pass


def list_sources() -> list[Source]:
    return list(Source.objects.order_by("id"))


def get_source(source_id: int) -> Source:
    try:
        return Source.objects.get(pk=source_id)
    except Source.DoesNotExist:
        raise SourceNotFound(f"없는 소스입니다: {source_id}") from None


def _validate(source: Source) -> None:
    """필드 검증과 `Source.clean()`(html 규칙)을 돌린다. 실패는 SourceInvalid."""
    try:
        source.full_clean()
    except ValidationError as exc:
        parts = [f"{field}: {' '.join(msgs)}" for field, msgs in exc.message_dict.items()]
        raise SourceInvalid("소스 값이 올바르지 않습니다. " + "; ".join(parts)) from exc


def create_source(
    *,
    name: str,
    kind: str,
    url: str,
    html_rules: Mapping[str, str] | None = None,
    enabled: bool = True,
) -> Source:
    source = Source(
        name=name.strip(),
        kind=kind,
        url=url.strip(),
        html_rules=dict(html_rules or {}),
        enabled=enabled,
        adapter_name="html" if kind == Source.Kind.HTML else "rss",
    )
    _validate(source)
    source.save()
    return source


@transaction.atomic
def update_source(
    source_id: int,
    *,
    name: str | None = None,
    kind: str | None = None,
    url: str | None = None,
    html_rules: Mapping[str, str] | None = None,
    enabled: bool | None = None,
) -> Source:
    """생략한 값은 그대로. 바뀐 결과 전체를 다시 검증한다(rss→html이면 규칙이 있어야 한다)."""
    source = get_source(source_id)
    if name is not None:
        source.name = name.strip()
    if url is not None:
        source.url = url.strip()
    if html_rules is not None:
        source.html_rules = dict(html_rules)
    if enabled is not None:
        source.enabled = enabled
    if kind is not None and kind != source.kind:
        source.kind = kind
        source.adapter_name = "html" if kind == Source.Kind.HTML else "rss"
    _validate(source)
    source.save()
    return source


def set_source_enabled(source_id: int, enabled: bool) -> Source:
    source = get_source(source_id)
    source.enabled = enabled
    source.save(update_fields=["enabled"])
    return source
