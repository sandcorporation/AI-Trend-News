"""유입 링크 `/go/sandcorp`, 기사 상세의 CTA·공유, admin 집계."""

import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http.response import HttpResponseBase as HttpResponse
from django.test import Client
from django.utils import timezone

from articles.models import OutboundClick
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"


def go(client: Client, query: str, agent: str = BROWSER, referer: str = "") -> HttpResponse:
    headers = {"User-Agent": agent}
    if referer:
        headers["Referer"] = referer
    return client.get(f"/go/sandcorp?{query}", headers=headers)


def test_inflow_link_redirects_with_utm_and_records_the_click(client: Client) -> None:
    article = make_article("금리 동결")

    response = go(
        client, f"from=article_cta&article={article.slug}", referer="https://news.example/a"
    )

    assert response.status_code == 302
    target = urlparse(response["Location"])
    assert (target.scheme, target.netloc) == ("https", "sandcorp.co.kr")
    assert parse_qs(target.query) == {
        "utm_source": ["news"],
        "utm_medium": ["article_cta"],
        "utm_campaign": [article.slug],
    }
    click = OutboundClick.objects.get()
    assert (click.location, click.article, click.referrer) == (
        "article_cta",
        article,
        "https://news.example/a",
    )
    assert click.user_agent == BROWSER


def test_without_article_the_campaign_is_site_and_article_is_null(client: Client) -> None:
    response = go(client, "from=header")

    assert parse_qs(urlparse(response["Location"]).query)["utm_campaign"] == ["site"]
    assert OutboundClick.objects.get().article is None


def test_unknown_or_unpublished_article_slug_is_ignored(client: Client) -> None:
    draft = make_article("초안", published=False)

    for slug in ("nope", draft.slug):
        response = go(client, f"from=footer&article={slug}")
        assert parse_qs(urlparse(response["Location"]).query)["utm_campaign"] == ["site"]
    assert OutboundClick.objects.filter(article__isnull=False).count() == 0


@pytest.mark.parametrize("query", ["from=nav", "", "article=x"])
def test_bad_location_is_400_and_not_recorded(client: Client, query: str) -> None:
    assert go(client, query).status_code == 400
    assert OutboundClick.objects.count() == 0


@pytest.mark.parametrize("agent", ["Googlebot/2.1", "SomeCrawler/1", "x spider y"])
def test_bots_are_redirected_but_not_recorded(client: Client, agent: str) -> None:
    response = go(client, "from=header", agent=agent)

    assert response.status_code == 302
    assert OutboundClick.objects.count() == 0


def test_record_failure_does_not_block_the_redirect(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(OutboundClick.objects, "create", boom)

    response = go(client, "from=footer")

    assert response.status_code == 302


def test_user_agent_and_referrer_are_truncated(client: Client) -> None:
    go(
        client,
        "from=header",
        agent="Mozilla " + "x" * 500,
        referer="https://a.example/" + "y" * 800,
    )

    click = OutboundClick.objects.get()
    assert len(click.user_agent) == 200 and len(click.referrer) == 500


def test_article_detail_has_cta_and_share(client: Client) -> None:
    article = make_article("금리 동결 & 환율")

    html = client.get(f"/a/{article.slug}").content.decode()

    assert 'data-slot="article-cta"' in html
    assert f"/go/sandcorp?from=article_cta&amp;article={article.slug}" in html
    assert 'href="/contact"' in html
    assert "Sandcorp 포트폴리오 보기" in html and "문의하기" in html
    assert "반복 업무를 자동화하고 싶으신가요?" in html
    x = re.search(r'href="(https://twitter\.com/intent/tweet\?[^"]+)"', html)
    assert x
    query = parse_qs(urlparse(x.group(1).replace("&amp;", "&")).query)
    assert query["text"] == ["금리 동결 & 환율"]
    assert query["url"] == [f"http://testserver/a/{article.slug}"]
    assert "링크 복사" in html
    page_data = json.loads(re.search(r'id="page-data"[^>]*>(.*?)</script>', html, re.S).group(1))  # type: ignore[union-attr]
    assert page_data["share"]["url"] == f"http://testserver/a/{article.slug}"
    assert page_data["cta"]["portfolio_url"].startswith("/go/sandcorp?from=article_cta")


def test_header_and_footer_links_are_inflow_links(client: Client) -> None:
    html = client.get("/").content.decode()

    assert 'href="/go/sandcorp?from=header"' in html
    assert 'href="/go/sandcorp?from=footer"' in html
    assert "https://sandcorp.co.kr" not in html


def test_admin_summary_counts_by_period_location_and_article(client: Client) -> None:
    article = make_article("집계 기사")
    for query in ("from=header", "from=header", f"from=article_cta&article={article.slug}"):
        go(client, query)
    old = OutboundClick.objects.create(location="footer")
    OutboundClick.objects.filter(pk=old.pk).update(clicked_at=timezone.now() - timedelta(days=10))
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)

    response = client.get(f"/{settings.ADMIN_PATH}articles/outboundclick/")

    assert response.status_code == 200
    rows = {s.key: s for s in response.context["summaries"]}
    assert [rows[k].total for k in ("today", "7d", "30d")] == [3, 3, 4]
    assert {r.label: r.count for r in rows["today"].by_location} == {"헤더": 2, "기사 하단 CTA": 1}
    assert {r.label: r.count for r in rows["7d"].by_article} == {"집계 기사": 1, "(기사 없음)": 2}
    html = response.content.decode()
    assert "유입 링크 클릭 집계" in html and "헤더 2" in html
