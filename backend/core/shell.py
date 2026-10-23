"""HTML shell views: Django renders per-route <head> meta, then React mounts on #root.

Add a route by adding a view here that builds a PageMeta and calls render_shell.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

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
        "body": body,
        "assets": entry_assets(),
    }
    return render(request, "core/shell.html", context, status=status)
