"""머리띠(#72): 서버 시계로 그린 오늘 날짜와 사이트 내비(분야·전망 기록표·문의, 현재 위치 표시)."""

import json
import re
from datetime import UTC, datetime

import pytest
from django.test import Client
from sandcorp_authkit.testing import FixedClock

from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)  # 금요일 12:00 KST


@pytest.fixture(autouse=True)
def now(frozen_clock: FixedClock) -> None:
    frozen_clock.moment = NOW


def site_of(html: str) -> dict[str, object]:
    match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    site: dict[str, object] = json.loads(match.group(1))["site"]
    return site


def current(site: dict[str, object]) -> list[str]:
    nav = site["nav"]
    assert isinstance(nav, list)
    return [n["label"] for n in nav if n["current"]]


def test_masthead_date_and_nav_on_every_page(client: Client) -> None:
    article = make_article("기사", category="AI")
    make_article("기사2", category="IT")

    html = client.get("/").content.decode()
    site = site_of(html)
    assert site["today_label"] == "2026.10.23 (금)"
    assert "2026.10.23 (금)" in html
    nav = site["nav"]
    assert isinstance(nav, list)
    assert [n["label"] for n in nav] == ["전체", "AI", "IT", "전망 기록표", "문의"]
    assert [n["url"] for n in nav][3:] == ["/predictions", "/contact"]
    assert current(site) == ["전체"]
    assert '<a href="/" aria-current="page">전체</a>' in html

    ai_url = nav[1]["url"]
    assert current(site_of(client.get(ai_url).content.decode())) == ["AI"]
    assert current(site_of(client.get("/predictions").content.decode())) == ["전망 기록표"]
    contact = client.get("/contact").content.decode()
    assert current(site_of(contact)) == ["문의"]
    assert '<a href="/contact" aria-current="page">문의</a>' in contact
    assert current(site_of(client.get(f"/a/{article.slug}").content.decode())) == []
