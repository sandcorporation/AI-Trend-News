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


def test_home_shell_preloads_first_screen_fonts_only(client: Client) -> None:
    html = client.get("/").content.decode()

    for name in (
        "PretendardVariable.subset.91-bbb222",
        "PretendardVariable.subset.90-ccc333",
        "jetbrains-mono-latin-wght-normal-ddd444",
    ):
        assert (
            f'<link rel="preload" as="font" type="font/woff2" '
            f'href="/static/assets/{name}.woff2" crossorigin>'
        ) in html
    assert "subset.0-aaa111" not in html
    assert "greek" not in html
    # fonts are requested before the stylesheet that names them
    assert html.index('rel="preload"') < html.index('rel="stylesheet"')


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


def test_head_has_metrics_snippet_when_configured(client: Client) -> None:
    with override_settings(METRICS_SNIPPET_URL="https://metrics.sandcorp.co.kr/m.js"):
        html = client.get("/").content.decode()

    assert (
        '<script defer src="https://metrics.sandcorp.co.kr/m.js" data-site="foresight"></script>'
        in html
    )
    assert html.index("m.js") < html.index("</head>")


def test_head_has_no_metrics_snippet_by_default(client: Client) -> None:
    assert "m.js" not in client.get("/").content.decode()


def test_head_has_naver_site_verification_once(client: Client) -> None:
    html = client.get("/").content.decode()
    tag = (
        '<meta name="naver-site-verification" content="8f87f757bdd2af4a28abb267601e24f6082da7c9" />'
    )
    assert html.count(tag) == 1


def test_head_has_sentry_config_when_frontend_dsn_set(client: Client) -> None:
    with override_settings(
        SENTRY_DSN_FRONTEND="https://public@example.invalid/2</script>&", SENTRY_RELEASE="b" * 40
    ):
        html = client.get("/").content.decode()

    found = re.search(r'<script type="application/json" id="sentry-config">(.*?)</script>', html)
    assert found is not None
    assert found.group(1) == (
        '{"dsn": "https://public@example.invalid/2\\u003C/script\\u003E\\u0026", '
        f'"release": "{"b" * 40}"}}'
    )
    assert html.index("sentry-config") < html.index('<script type="module"')


def test_head_has_no_sentry_config_without_frontend_dsn(client: Client) -> None:
    assert "sentry-config" not in client.get("/").content.decode()
