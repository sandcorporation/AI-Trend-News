"""HTML shell views: Django renders per-route <head> meta, then React mounts on #root.

Add a route by adding a view here that builds a PageMeta and calls render_shell.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils.safestring import SafeString, mark_safe

from core.vite import entry_assets

SITE_NAME = "Foresight"
SITE_DESCRIPTION = "AI와 IT 뉴스를 한국어로 골라 요약하고, 전망을 적어 채점합니다"
INDEX = "index, follow"
NOINDEX = "noindex, nofollow"


# 이미지 없는 페이지의 공유 카드 폴백(1200x630, scripts/og-image/build.sh로 만든다).
DEFAULT_OG_IMAGE = "core/og-default.png"


@dataclass(frozen=True)
class PageMeta:
    title: str
    description: str
    robots: str
    image: str = ""
    image_alt: str = ""
    # 이미지가 1200x630임을 아는 경우(직접 만든 카드). 원문 이미지는 크기를 모른다.
    image_sized: bool = False
    # 기사 페이지만: ISO 8601 문자열, 분야 이름, 태그.
    published_time: str = ""
    modified_time: str = ""
    section: str = ""
    tags: tuple[str, ...] = ()
    # JSON-LD 객체들(각각 <script type="application/ld+json"> 하나).
    json_ld: tuple[Mapping[str, object], ...] = ()


_LD_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}


def ld_json(value: Mapping[str, object]) -> SafeString:
    """JSON-LD 본문. `<`·`>`·`&`를 유니코드 이스케이프해 `</script>`로 태그가 깨지지 않게 한다."""
    return mark_safe(json.dumps(value, ensure_ascii=False).translate(_LD_ESCAPES))  # noqa: S308


def sentry_public_config() -> SafeString | None:
    """프런트 오류 수집(#168) 공개 설정 JSON. DSN이 비면 None(셸에 아무것도 내지 않는다).

    Vite 빌드에 박지 않고 실행 env에서 넘긴다 — 이미지 하나를 sha로 그대로 배포하기 때문이다.
    """
    dsn = settings.SENTRY_DSN_FRONTEND
    if not dsn:
        return None
    return ld_json({"dsn": dsn, "release": settings.SENTRY_RELEASE})


def render_shell(
    request: HttpRequest,
    meta: PageMeta,
    status: int = 200,
    data: Mapping[str, object] | None = None,
    body: str = "",
    canonical: str = "",
    og_type: str = "website",
) -> HttpResponse:
    """Render the shell. `data` is embedded as JSON in #page-data for React's first render.

    `body` is server-rendered HTML placed inside #root (crawlers read it; React replaces it).
    """
    context = {
        "site_name": SITE_NAME,
        "data": data,
        "meta": meta,
        "og_image": request.build_absolute_uri(meta.image or static(DEFAULT_OG_IMAGE)),
        # 기본 이미지만 크기를 안다(원문 이미지는 모른다).
        "og_image_is_default": not meta.image or meta.image_sized,
        "og_image_alt": meta.image_alt or meta.title,
        "canonical": canonical or request.build_absolute_uri(request.path),
        "og_type": og_type,
        "twitter_card": "summary_large_image",
        "twitter_site": settings.SITE_TWITTER,
        "ld_blocks": [ld_json(block) for block in meta.json_ld],
        "metrics_snippet_url": settings.METRICS_SNIPPET_URL,
        "metrics_site_key": settings.METRICS_SITE_KEY,
        "sentry_config": sentry_public_config(),
        "body": body,
        "assets": entry_assets(),
    }
    return render(request, "core/shell.html", context, status=status)
