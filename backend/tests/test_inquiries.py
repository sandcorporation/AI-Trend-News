"""문의 폼 → portfolio 전달(ADR 0003). 경계는 /contact HTTP와 resend_inquiries 명령, 가짜 어댑터."""

import json
from datetime import timedelta

import pytest
from bs4 import BeautifulSoup
from django.core.management import call_command
from django.test import Client, override_settings
from sandcorp_authkit.testing import FixedClock

from adapters.portfolio import (
    FakeGateway,
    PermanentError,
    PortfolioGateway,
    TransientError,
)
from inquiries.models import Inquiry, SubmitAttempt
from services.scheduler import COMMANDS

pytestmark = pytest.mark.django_db

FORM = {
    "name": "홍길동",
    "company": "샌드",
    "contact": "hong@example.com",
    "message": "자동화 문의드립니다.",
    "kind": "문의",
}


def post(client: Client, ip: str = "203.0.113.9", **overrides: str):  # type: ignore[no-untyped-def]
    """제출하고, 성공(303)이면 따라가 성공 화면을 돌려준다."""
    return client.post("/contact", {**FORM, **overrides}, follow=True, HTTP_X_FORWARDED_FOR=ip)


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


@pytest.mark.parametrize("overrides", [{}, {"website": "http://spam.example"}])
def test_success_redirects_so_reload_does_not_resubmit(
    client: Client, fake_portfolio: FakeGateway, overrides: dict[str, str]
) -> None:
    response = client.post("/contact", {**FORM, **overrides})
    assert response.status_code == 303
    assert response["Location"] == "/contact?sent=1"
    sent = len(fake_portfolio.sent)

    for _ in range(2):  # 성공 화면과 그 새로고침
        done = client.get(response["Location"])
        assert done.status_code == 200
        assert done["Cache-Control"] == "no-store"
        page = BeautifulSoup(done.content, "html.parser")
        status = page.select_one('[data-slot="contact"] [role="status"]')
        assert status is not None and "접수했습니다" in status.get_text()
        assert not page.select("form.contact-form")
        data = page.select_one("#page-data")
        assert data is not None and json.loads(data.get_text())["state"] == "done"
    assert len(fake_portfolio.sent) == sent


@pytest.mark.parametrize(
    "contact",
    [
        "hong@example.com",
        " a.b+c@mail.example.co.kr ",
        "010-1234-5678",
        "+82 10 1234 5678",
        "(02) 123-4567",
        "021234567",
    ],
)
def test_email_or_phone_contact_is_accepted(
    client: Client, fake_portfolio: FakeGateway, contact: str
) -> None:
    assert "접수했습니다" in post(client, contact=contact).content.decode()
    assert len(fake_portfolio.sent) == 1


@pytest.mark.parametrize(
    "contact",
    [
        "abc",
        "010",
        "12345678",
        "kbd@example",
        "kbd@.com",
        "kbd@example.",
        "a b@example.com",
        "@example.com",
        "010-1234-5678 홍길동",
        "---------",
    ],
)
def test_unreachable_contact_is_rejected(
    client: Client, fake_portfolio: FakeGateway, contact: str
) -> None:
    response = post(client, contact=contact)
    assert response.status_code == 400
    page = BeautifulSoup(response.content, "html.parser")
    error = page.select_one("#contact-contact-error")
    assert error is not None and error.get_text() == "이메일이나 전화번호를 적어 주세요."
    assert Inquiry.objects.count() == 0 and fake_portfolio.sent == []
    assert SubmitAttempt.objects.count() == 0


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
    client: Client, frozen_clock: FixedClock
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
    assert "이름을 입력해 주세요." in html and "내용을 입력해 주세요." in html
    assert Inquiry.objects.count() == 0 and fake_portfolio.sent == []


def test_errors_are_tied_to_fields_and_summarized_for_screen_readers(client: Client) -> None:
    response = post(client, name=" ", company="가" * 101, kind="없는 종류")
    assert response.status_code == 400
    html = response.content.decode()
    page = BeautifulSoup(html, "html.parser")

    summary = page.select_one(".error-summary")
    assert summary is not None
    assert summary["role"] == "alert" and summary["tabindex"] == "-1"
    assert summary.has_attr("autofocus")
    links = [str(a["href"]) for a in summary.select("a")]
    assert links == ["#contact-kind", "#contact-name", "#contact-company"]
    assert all(page.select_one(href) for href in links)

    for field in ("kind", "name", "company"):
        control = page.select_one(f"#contact-{field}")
        assert control is not None
        assert control["aria-invalid"] == "true"
        assert control["aria-describedby"] == f"contact-{field}-error"
        assert page.select_one(f"#contact-{field}-error")
    assert page.select_one("fieldset.kind #contact-kind-error")
    for field in ("contact", "message"):
        control = page.select_one(f"#contact-{field}")
        assert control is not None and not control.has_attr("aria-invalid")

    data = page.select_one("#page-data")
    assert data is not None
    assert [e["href"] for e in json.loads(data.get_text())["error_summary"]] == links


def test_form_without_errors_has_no_summary(client: Client) -> None:
    html = client.get("/contact").content.decode()
    page = BeautifulSoup(html, "html.parser")
    assert not page.select(".error-summary, [aria-invalid], [aria-describedby], [autofocus]")


def test_invalid_submissions_do_not_count_toward_rate_limit(client: Client) -> None:
    for _ in range(5):
        assert post(client, message="").status_code == 400
    for _ in range(5):
        assert post(client).status_code == 200
    assert post(client).status_code == 429


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
