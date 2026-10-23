"""HTML shell views: Django renders per-route <head> meta, then React mounts on #root.

Add a route by adding a view here that builds a PageMeta and calls render_shell.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.safestring import SafeString, mark_safe

from core.vite import entry_assets

SITE_NAME = "Foresight"
SITE_DESCRIPTION = "AI와 IT 뉴스를 한국어로 골라 요약하고, 전망을 적어 채점합니다"
INDEX = "index, follow"
NOINDEX = "noindex, nofollow"


@dataclass(frozen=True)
class PageMeta:
    title: str
    description: str
    robots: str
    image: str = ""
    image_alt: str = ""
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
        "og_image": request.build_absolute_uri(meta.image) if meta.image else "",
        "canonical": canonical or request.build_absolute_uri(request.path),
        "og_type": og_type,
        "twitter_card": "summary_large_image" if meta.image else "summary",
        "twitter_site": settings.SITE_TWITTER,
        "ld_blocks": [ld_json(block) for block in meta.json_ld],
        "body": body,
        "assets": entry_assets(),
    }
    return render(request, "core/shell.html", context, status=status)
