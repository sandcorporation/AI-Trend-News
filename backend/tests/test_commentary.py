"""AI 분석(#51): 서비스 규칙, MCP `upsert_commentary`·`has_commentary`, 공개 조회 조건."""

from typing import TypedDict

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from articles.models import Article, Commentary
from mcpserver.jsontypes import JSON
from services import articles as article_service
from services import commentary as svc
from services.articles import Actor
from tests.conftest import Signer
from tests.test_home_layout import page_data
from tests.test_mcp import McpClient, make_items
from tests.test_public_site import make_article as make_public


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="editor"))


def make_article(mcp: McpClient, **flags: bool) -> dict[str, JSON]:
    category = article_service.add_category("IT").slug
    item = make_items(1)[0]
    return mcp.ok(
        "create_article",
        {
            "item_ids": [item.pk],
            "title": "금리 동결",
            "summary": "요약",
            "category": category,
            "slug": "rates-frozen",
            **({"published": True, "featured": True} | flags),
        },
    )


def args(article: dict[str, JSON], **over: JSON) -> dict[str, JSON]:
    base: dict[str, JSON] = {
        "article_id": article["id"],
        "background": "물가 둔화가 이어졌다.",
        "significance": "대출 금리 부담이 달라질 수 있다.",
        "outlook_bull": "물가가 계속 둔화된다면 인하 기대가 커질 가능성이 크다.",
        "outlook_bear": "환율이 불안하면 인하가 늦어질 가능성이 있다.",
        "opinion": "당분간 동결 기조가 이어질 것으로 보인다.",
        "predictions": [
            {"claim": "기준금리가 6개월 안에 내린다", "horizon": "6m", "basis": "물가 둔화"}
        ],
    }
    return base | over


class Fields(TypedDict):
    background: str
    significance: str
    outlook_bull: str
    outlook_bear: str
    opinion: str
    predictions: list[svc.PredictionIn]


def kwargs() -> Fields:
    return {
        "background": "b",
        "significance": "s",
        "outlook_bull": "u",
        "outlook_bear": "d",
        "opinion": "o",
        "predictions": [],
    }


def has_commentary_flags(mcp: McpClient) -> list[JSON]:
    listed = mcp.ok("list_recent_articles", {"days": 7})["articles"]
    assert isinstance(listed, list)
    flags: list[JSON] = []
    for entry in listed:
        assert isinstance(entry, dict)
        flags.append(entry["has_commentary"])
    return flags


@pytest.mark.django_db
def test_upsert_creates_then_updates_in_place_and_lists_has_commentary(mcp: McpClient) -> None:
    article = make_article(mcp)
    assert has_commentary_flags(mcp) == [False]

    first = mcp.ok("upsert_commentary", args(article))
    assert first["hidden"] is False
    assert first["predictions"] == [
        {"claim": "기준금리가 6개월 안에 내린다", "horizon": "6m", "basis": "물가 둔화"}
    ]
    mcp.ok("upsert_commentary", args(article, opinion="고친 의견"))
    assert Commentary.objects.count() == 1 and Commentary.objects.get().opinion == "고친 의견"
    assert has_commentary_flags(mcp) == [True]


@pytest.mark.django_db
@pytest.mark.parametrize("flags", [{"featured": False}, {"published": False}])
def test_rejected_for_non_featured_or_unpublished(mcp: McpClient, flags: dict[str, bool]) -> None:
    article = make_article(mcp, **flags)
    assert "추천이면서 게시된" in mcp.fails("upsert_commentary", args(article))
    assert not Commentary.objects.exists()


@pytest.mark.django_db
def test_hidden_commentary_is_not_overwritten_by_editor(mcp: McpClient) -> None:
    article = make_article(mcp)
    mcp.ok("upsert_commentary", args(article))
    Commentary.objects.update(hidden=True)
    assert "숨겼" in mcp.fails("upsert_commentary", args(article, opinion="덮어쓰기"))
    assert Commentary.objects.get().opinion != "덮어쓰기"
    owner = svc.upsert_commentary(Actor.OWNER, Article.objects.get().pk, **kwargs())
    assert owner.hidden is True  # 대표는 고치되 숨김 상태는 그대로


@pytest.mark.django_db
def test_length_and_prediction_validation(mcp: McpClient) -> None:
    article = make_article(mcp)
    assert "400" in mcp.fails("upsert_commentary", args(article, background="가" * 401))
    assert "120" in mcp.fails("upsert_commentary", args(article, opinion="가" * 121))
    assert "1~" in mcp.fails("upsert_commentary", args(article, significance="  "))
    p: dict[str, JSON] = {"claim": "x가 오른다", "horizon": "3m", "basis": "근거"}
    assert "3개까지" in mcp.fails("upsert_commentary", args(article, predictions=[p] * 4))
    assert "예측 기간" in mcp.fails(
        "upsert_commentary", args(article, predictions=[p | {"horizon": "2m"}])
    )
    assert "120" in mcp.fails(
        "upsert_commentary", args(article, predictions=[p | {"claim": "가" * 121}])
    )
    assert "200" in mcp.fails(
        "upsert_commentary", args(article, predictions=[p | {"basis": "가" * 201}])
    )
    assert not Commentary.objects.exists()
    mcp.ok("upsert_commentary", args(article, predictions=[]))  # 0개는 허용


@pytest.mark.django_db
def test_public_commentary_conditions_and_survival(mcp: McpClient) -> None:
    article = make_article(mcp)
    mcp.ok("upsert_commentary", args(article))
    obj = Article.objects.get()
    assert svc.public_commentary(obj) is not None

    Commentary.objects.update(hidden=True)
    assert svc.public_commentary(Article.objects.get()) is None
    Commentary.objects.update(hidden=False)

    obj.featured = False  # 추천이 풀려도 분석은 남고 게시 상태면 보인다
    obj.save()
    assert Commentary.objects.count() == 1 and svc.public_commentary(obj) is not None
    obj.published = False
    obj.save()
    assert Commentary.objects.count() == 1 and svc.public_commentary(obj) is None


# --- 화면(#51): 서버 HTML과 #page-data ---


def _commentary_for(article: Article) -> Commentary:
    return svc.upsert_commentary(
        Actor.OWNER,
        article.pk,
        background="물가 둔화가 이어졌다.",
        significance="대출 부담이 달라진다.",
        outlook_bull="낙관 전제 문장",
        outlook_bear="회의 전제 문장",
        opinion="당분간 동결이다.",
        predictions=[
            svc.PredictionIn(claim="금리가 내린다", horizon="3m", basis="물가 둔화"),
        ],
    )


@pytest.mark.django_db
def test_article_page_shows_commentary_in_html_and_page_data(client: Client) -> None:
    article = make_public("분석 있는 기사")
    _commentary_for(article)
    html = client.get(f"/a/{article.slug}").content.decode()

    assert 'data-slot="commentary"' in html and "AI 분석" in html
    assert (
        "편집자 AI가 수집된 보도만을 근거로 쓴 분석입니다. 사실 확인은 출처를 참고하세요." in html
    )
    for text in ("물가 둔화가 이어졌다.", "낙관 전제 문장", "회의 전제 문장", "당분간 동결이다."):
        assert text in html
    assert "3개월 안" in html and "금리가 내린다" in html
    assert html.index('class="summary"') < html.index('data-slot="commentary"')
    assert html.index('data-slot="commentary"') < html.index('id="sources-h"')
    data = page_data(html)
    article_data = data["article"]
    assert isinstance(article_data, dict)
    commentary = article_data["commentary"]
    assert isinstance(commentary, dict) and commentary["opinion"] == "당분간 동결이다."
    assert commentary["predictions"] == [
        {
            "claim": "금리가 내린다",
            "horizon": "3m",
            "horizon_label": "3개월 안",
            "basis": "물가 둔화",
            "due_label": commentary["predictions"][0]["due_label"],
            "verdict": "",
            "verdict_label": "",
            "verdict_note": "",
        }
    ]


@pytest.mark.django_db
def test_article_page_without_or_hidden_commentary_has_no_block(client: Client) -> None:
    plain = make_public("분석 없는 기사")
    hidden = make_public("숨긴 기사")
    _commentary_for(hidden)
    Commentary.objects.update(hidden=True)

    for article in (plain, hidden):
        html = client.get(f"/a/{article.slug}").content.decode()
        assert 'data-slot="commentary"' not in html
        article_data = page_data(html)["article"]
        assert isinstance(article_data, dict) and article_data["commentary"] is None
        assert article_data["has_commentary"] is False


@pytest.mark.django_db
def test_home_cards_show_badge_only_with_public_commentary_and_query_budget(
    client: Client,
) -> None:
    with_analysis = make_public("분석 기사")
    hidden = make_public("숨긴 기사")
    for n in range(4):
        make_public(f"기사 {n}")
    _commentary_for(with_analysis)
    _commentary_for(hidden)
    Commentary.objects.filter(article=hidden).update(hidden=True)

    with CaptureQueriesContext(connection) as ctx:
        html = client.get("/").content.decode()
    assert len(ctx) <= 10, [q["sql"][:80] for q in ctx]
    assert html.count('class="badge-analysis"') == 1
    cards = page_data(html)["articles"]
    assert isinstance(cards, list)
    flags = {c["title"]: c["has_commentary"] for c in cards if isinstance(c, dict)}
    assert flags["분석 기사"] is True and flags["숨긴 기사"] is False


@pytest.mark.django_db
def test_article_page_adds_at_most_one_query_for_commentary(client: Client) -> None:
    plain = make_public("분석 없는 기사")
    with_analysis = make_public("분석 있는 기사")
    _commentary_for(with_analysis)

    def count(article: Article) -> int:
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(f"/a/{article.slug}").status_code == 200
        return len(ctx)

    assert count(with_analysis) <= count(plain) + 1
