"""문의 폼 → portfolio 전달(ADR 0003). 경계는 /contact HTTP와 resend_inquiries 명령, 가짜 어댑터."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client, override_settings

from adapters.portfolio import (
    FakeGateway,
    PermanentError,
    PortfolioGateway,
    TransientError,
)
from inquiries.models import Inquiry, SubmitAttempt
from services.scheduler import COMMANDS
from tests.conftest import FrozenClock

pytestmark = pytest.mark.django_db

FORM = {
    "name": "홍길동",
    "company": "샌드",
    "contact": "hong@example.com",
    "message": "자동화 문의드립니다.",
    "kind": "문의",
}


def post(client: Client, ip: str = "203.0.113.9", **overrides: str):  # type: ignore[no-untyped-def]
    return client.post("/contact", {**FORM, **overrides}, HTTP_X_FORWARDED_FOR=ip)


def test_form_page_renders_and_is_noindex(client: Client) -> None:
    response = client.get("/contact")
    html = response.content.decode()
    assert response.status_code == 200
    assert 'name="message"' in html and 'name="website"' in html
    assert "noindex" in html


def test_submit_reaches_portfolio_with_site_and_kind(
    client: Client, fake_portfolio: FakeGateway
) -> None:
    response = post(client, kind="제안")
    assert response.status_code == 200
    assert "접수했습니다" in response.content.decode()
    assert fake_portfolio.sent == [{**FORM, "kind": "제안", "site": "news"}]
    assert Inquiry.objects.count() == 0


def test_transient_failure_keeps_it_queued_then_resend_clears_it(
    client: Client, fake_portfolio: FakeGateway
) -> None:
    fake_portfolio.error = TransientError("HTTP 503")
    response = post(client)
    assert "접수했습니다" in response.content.decode()
    queued = Inquiry.objects.get()
    assert (queued.status, queued.attempts, queued.last_error) == ("pending", 1, "HTTP 503")

    fake_portfolio.error = None
    call_command("resend_inquiries")
    assert Inquiry.objects.count() == 0
    assert len(fake_portfolio.sent) == 1


def test_permanent_failure_stays_and_is_not_resent(
    client: Client, fake_portfolio: FakeGateway
) -> None:
    fake_portfolio.error = PermanentError("HTTP 400")
    assert "접수했습니다" in post(client).content.decode()
    failed = Inquiry.objects.get()
    assert (failed.status, failed.last_error) == ("failed", "HTTP 400")

    fake_portfolio.error = None
    call_command("resend_inquiries")
    assert Inquiry.objects.get().status == "failed"
    assert fake_portfolio.sent == []


def test_honeypot_is_neither_stored_nor_sent_but_looks_successful(
    client: Client, fake_portfolio: FakeGateway
) -> None:
    response = post(client, website="http://spam.example")
    assert "접수했습니다" in response.content.decode()
    assert Inquiry.objects.count() == 0 and fake_portfolio.sent == []
    assert SubmitAttempt.objects.count() == 0


def test_rate_limit_is_per_ip_over_15_minutes_and_uses_last_forwarded_hop(
    client: Client, frozen_clock: FrozenClock
) -> None:
    for _ in range(5):
        assert post(client).status_code == 200
    assert post(client).status_code == 429
    # 왼쪽 칸을 꾸며도 오른쪽 끝(프록시가 본 주소)으로 센다.
    forged = client.post("/contact", FORM, HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9")
    assert forged.status_code == 429
    assert post(client, ip="198.51.100.7").status_code == 200
    SubmitAttempt.objects.update(at=frozen_clock.now() - timedelta(minutes=16))
    assert post(client).status_code == 200


def test_invalid_input_is_rejected_with_field_errors(
    client: Client, fake_portfolio: FakeGateway
) -> None:
    response = post(client, name=" ", message="")
    assert response.status_code == 400
    html = response.content.decode()
    assert "이름을(를) 입력해 주세요." in html and "내용을(를) 입력해 주세요." in html
    assert Inquiry.objects.count() == 0 and fake_portfolio.sent == []


def test_unconfigured_only_queues(client: Client, fake_portfolio: FakeGateway) -> None:
    with override_settings(PORTFOLIO_INQUIRY_URL="", PORTFOLIO_INQUIRY_SECRET=""):
        assert "접수했습니다" in post(client).content.decode()
        call_command("resend_inquiries")
    assert fake_portfolio.sent == []
    assert Inquiry.objects.get().status == "pending"


def test_resend_is_scheduled() -> None:
    assert "resend_inquiries" in COMMANDS


def test_contact_links_on_article_cta_and_footer(client: Client) -> None:
    from tests.test_public_site import make_article  # noqa: PLC0415

    article = make_article("문의 링크 기사")
    html = client.get(f"/a/{article.slug}").content.decode()
    assert 'href="/contact"' in html
    assert html.count('href="/contact"') >= 2  # CTA + footer (server HTML), page-data too
    assert "/go/sandcorp?from=contact" not in html
    assert 'href="/contact"' in client.get("/").content.decode()


class RecordingPost:
    def __init__(self, result: int | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, str, bytes]] = []

    def __call__(self, url: str, secret: str, body: bytes) -> int:
        self.calls.append((url, secret, body))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


payload = {**FORM, "site": "news"}


def test_real_gateway_sends_secret_header_and_maps_statuses() -> None:
    ok = RecordingPost(201)
    PortfolioGateway("https://p/api/inquiries", "s3", ok).send(payload)  # type: ignore[arg-type]
    assert ok.calls[0][:2] == ("https://p/api/inquiries", "s3")
    with pytest.raises(PermanentError):
        PortfolioGateway("u", "s", RecordingPost(422)).send(payload)  # type: ignore[arg-type]
    with pytest.raises(TransientError):
        PortfolioGateway("u", "s", RecordingPost(503)).send(payload)  # type: ignore[arg-type]
    with pytest.raises(TransientError):
        PortfolioGateway("u", "s", RecordingPost(TimeoutError("x"))).send(payload)  # type: ignore[arg-type]
