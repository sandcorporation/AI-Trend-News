"""HTML shell views: Django renders per-route <head> meta, then React mounts on #root.

Add a route by adding a view here that builds a PageMeta and calls render_shell.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from core.vite import entry_assets

SITE_NAME = "AI Trend News"
SITE_DESCRIPTION = "여러 분야의 뉴스를 한국어로 골라 요약해 보여 줍니다."
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
) -> HttpResponse:
    """Render the shell. `data` is embedded as JSON in #page-data for React's first render."""
    context = {
        "site_name": SITE_NAME,
        "data": data,
        "meta": meta,
        "og_image": request.build_absolute_uri(meta.image) if meta.image else "",
        "canonical": request.build_absolute_uri(request.path),
        "assets": entry_assets(),
    }
    return render(request, "core/shell.html", context, status=status)


def home(request: HttpRequest) -> HttpResponse:
    return render_shell(
        request,
        PageMeta(title=SITE_NAME, description=SITE_DESCRIPTION, robots=INDEX),
    )
