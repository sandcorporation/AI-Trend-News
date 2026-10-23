"""원문 읽기(#60): 고정 HTML에서 본문 추출, 실패는 빈 text+reason, 8000자 절단, MCP `read_item`."""

from pathlib import Path

import pytest
from django.test import Client

from adapters.fetch import FetchError
from collection.models import Item
from services import reading
from tests.conftest import FakeFetcher, Signer
from tests.test_mcp import McpClient, make_items

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="editor"))


def test_extracts_article_body_without_noise() -> None:
    text = reading.extract_text((FIXTURES / "article-page.html").read_bytes())
    paragraphs = text.split("\n\n")
    assert paragraphs[0] == "엔비디아, 새 추론 칩 공개"
    assert (
        "엔비디아가 22일 데이터센터용 새 추론 칩을 공개했다. "
        "회사는 이전 세대보다 전력당 처리량이 두 배라고 밝혔다." in paragraphs
    )
    assert "가격은 &공개되지 않았다." in text  # 엔티티 해제, 공백 한 칸
    assert "경쟁 구도" in paragraphs
    assert "전력당 처리량 2배" in paragraphs
    for noise in (
        "많이 본 뉴스",
        "Copyright",
        "로그인",
        "trackRead",
        "analytics",
        "새 칩 사진",
        "공유",
    ):
        assert noise not in text


def test_falls_back_to_paragraph_density_without_semantic_tags() -> None:
    text = reading.extract_text((FIXTURES / "article-divs.html").read_bytes())
    assert text.split("\n\n") == [
        "오픈소스 데이터베이스 프로젝트가 새 메이저 버전을 냈다. 질의 계획기가 다시 쓰였다.",
        "개발팀은 대표 벤치마크에서 질의 속도가 평균 30% 빨라졌다고 밝혔다.",
        "기존 확장과의 호환성은 대부분 유지되지만 일부 내부 API가 바뀌었다.",
    ]


@pytest.mark.django_db
def test_read_item_returns_text_and_does_not_store_it() -> None:
    item = make_items(1)[0]
    fetcher = FakeFetcher({item.url: (FIXTURES / "article-page.html").read_bytes()})
    result = reading.read_item(item.pk, fetcher)
    assert result["item_id"] == item.pk
    assert result["url"] == item.url
    assert result["reason"] == ""
    assert result["truncated"] is False
    assert "출시는 내년 1분기로" in result["text"]
    item.refresh_from_db()
    assert item.excerpt == "excerpt 0"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (FetchError("HTTPError: HTTP Error 403: Forbidden"), "403"),
        (FetchError("TimeoutError: timed out"), "timed out"),
        (b"<html><body><nav>menu</nav><p>short</p></body></html>", "본문을 찾지 못했습니다"),
        (b"", "본문을 찾지 못했습니다"),
    ],
)
def test_failures_are_empty_text_with_reason(response: bytes | Exception, reason: str) -> None:
    item = make_items(1)[0]
    result = reading.read_item(item.pk, FakeFetcher({item.url: response}))
    assert result["text"] == ""
    assert reason in result["reason"]


@pytest.mark.django_db
def test_long_body_is_cut_at_8000_chars_and_only_first_megabyte_is_read() -> None:
    item = make_items(1)[0]
    body = "".join(f"<p>{'가' * 99}</p>" for _ in range(200))
    html = f"<html><body><article>{body}</article></body></html>".encode()
    result = reading.read_item(item.pk, FakeFetcher({item.url: html}))
    assert len(result["text"]) == reading.MAX_TEXT_LENGTH
    assert result["truncated"] is True

    tail = b"<p>" + "나".encode() * 100 + b"</p>"
    huge = b"<html><body><article><p>" + b"a" * 200 + b"</p>" + b" " * 1_000_000 + tail
    result = reading.read_item(item.pk, FakeFetcher({item.url: huge}))
    assert "나" not in result["text"]


@pytest.mark.django_db
def test_mcp_read_item(mcp: McpClient, fake_fetcher: FakeFetcher) -> None:
    item = make_items(1)[0]
    fake_fetcher.responses[item.url] = (FIXTURES / "article-divs.html").read_bytes()
    found = mcp.ok("read_item", {"item_id": item.pk})
    assert found["title"] == item.title
    assert isinstance(found["text"], str) and found["text"].startswith("오픈소스")
    assert fake_fetcher.requested == [item.url]
    assert "없는 수집물" in mcp.fails("read_item", {"item_id": 999_999})
    assert Item.objects.count() == 1
