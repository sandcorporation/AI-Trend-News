import re

import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db


def meta(html: str, name: str) -> str | None:
    found = re.search(rf'<meta name="{name}" content="([^"]*)"', html)
    return found.group(1) if found else None


def og(html: str, prop: str) -> str | None:
    found = re.search(rf'<meta property="og:{prop}" content="([^"]*)"', html)
    return found.group(1) if found else None


def test_home_shell_is_indexable_and_mounts_the_vite_bundle(client: Client) -> None:
    response = client.get("/")
    html = response.content.decode()

    assert response.status_code == 200
    assert "<title>Foresight</title>" in html
    assert meta(html, "robots") == "index, follow"
    assert '<div id="root">' in html
    assert '<script type="module" src="/static/assets/main-abc123.js"></script>' in html
    assert '<link rel="stylesheet" href="/static/assets/main-def456.css">' in html


def test_home_shell_carries_description_and_og(client: Client) -> None:
    html = client.get("/").content.decode()

    assert meta(html, "description")
    assert og(html, "title") == "Foresight"
    assert og(html, "description") == meta(html, "description")
    assert og(html, "url") == "http://testserver/"
    assert '<link rel="canonical" href="http://testserver/">' in html


def test_home_shell_points_at_the_vite_dev_server_in_development(client: Client) -> None:
    with override_settings(VITE_DEV_SERVER_URL="http://localhost:5173"):
        html = client.get("/").content.decode()

    assert '<script type="module" src="http://localhost:5173/@vite/client"></script>' in html
    assert '<script type="module" src="http://localhost:5173/src/main.tsx"></script>' in html


def test_no_old_site_name_left_in_templates_and_frontend() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    roots = [root / "backend" / "core" / "templates", root / "frontend" / "src"]
    for base in roots:
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".html", ".tsx", ".ts"}:
                assert "AI Trend News" not in path.read_text(encoding="utf-8"), path


def test_head_links_the_rss_feed(client: Client) -> None:
    html = client.get("/").content.decode()
    assert (
        '<link rel="alternate" type="application/rss+xml" title="Foresight" href="/feed.xml">'
        in html
    )
