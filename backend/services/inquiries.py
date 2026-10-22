"""문의 접수·전달 규칙(ADR 0003). 이 사이트는 저장하지 않고 portfolio로 전달만 한다.

제출 → (honeypot이면 조용히 성공) → IP 요청 제한 → 검증 → 대기열에 넣고 즉시 1회 전송.
성공하면 지우고, 재시도할 실패면 대기로 남기며, 4xx면 영구실패로 표시해 admin에 남긴다.
로그에는 id·상태만 남기고 내용·연락처는 남기지 않는다.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from adapters.portfolio import (
    SITE,
    InquiryPayload,
    PermanentError,
    TransientError,
    get_gateway,
    is_configured,
)
from inquiries.models import Inquiry, SubmitAttempt

logger = logging.getLogger("inquiries")

RATE_WINDOW = timedelta(minutes=15)
RATE_LIMIT = 5
LIMITS = {"name": 50, "company": 100, "contact": 200, "message": 5000}


class RateLimited(Exception):
    pass


@dataclass
class InquiryInvalid(Exception):
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Submission:
    name: str
    company: str
    contact: str
    message: str
    kind: str
    honeypot: str = ""


def _validate(s: Submission) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key, label in (("name", "이름"), ("contact", "연락처"), ("message", "내용")):
        if not getattr(s, key).strip():
            errors[key] = f"{label}을(를) 입력해 주세요."
    for key, limit in LIMITS.items():
        if len(getattr(s, key)) > limit:
            errors.setdefault(key, f"{limit}자 이하로 입력해 주세요.")
    if s.kind not in Inquiry.Kind.values:
        errors["kind"] = "종류를 고르세요."
    return errors


def _check_rate(ip: str, now: datetime) -> None:
    SubmitAttempt.objects.filter(at__lt=now - RATE_WINDOW).delete()
    recent = SubmitAttempt.objects.filter(ip=ip, at__gte=now - RATE_WINDOW).count()
    if recent >= RATE_LIMIT:
        raise RateLimited
    SubmitAttempt.objects.create(ip=ip, at=now)


def submit(s: Submission, ip: str, now: datetime) -> None:
    """접수한다. honeypot이 차 있으면 아무것도 하지 않고 돌아온다(겉으로는 성공)."""
    if s.honeypot.strip():
        return
    _check_rate(ip, now)
    errors = _validate(s)
    if errors:
        raise InquiryInvalid(errors)
    inquiry = Inquiry.objects.create(
        name=s.name.strip(),
        company=s.company.strip(),
        contact=s.contact.strip(),
        message=s.message.strip(),
        kind=s.kind,
    )
    deliver(inquiry, now)


def _payload(inquiry: Inquiry) -> InquiryPayload:
    return {
        "name": inquiry.name,
        "company": inquiry.company,
        "contact": inquiry.contact,
        "message": inquiry.message,
        "site": SITE,
        "kind": inquiry.kind,
    }


def deliver(inquiry: Inquiry, now: datetime) -> bool:
    """한 건을 보낸다. 보내서 지웠으면 True. 설정이 비면 시도하지 않고 대기로 둔다."""
    if inquiry.status != Inquiry.Status.PENDING:
        return False
    if not is_configured():
        logger.warning("문의 %s: PORTFOLIO_INQUIRY_* 설정 없음 — 대기열에만 둔다", inquiry.pk)
        return False
    inquiry.attempts += 1
    try:
        get_gateway().send(_payload(inquiry))
    except PermanentError as error:
        inquiry.status = Inquiry.Status.FAILED
        inquiry.last_error = str(error)[:200]
        inquiry.save(update_fields=["status", "attempts", "last_error"])
        logger.error("문의 %s: 영구 실패 (%s)", inquiry.pk, inquiry.last_error)
        return False
    except TransientError as error:
        inquiry.last_error = str(error)[:200]
        inquiry.save(update_fields=["attempts", "last_error"])
        logger.warning("문의 %s: 전송 실패, 재시도 대기 (%s)", inquiry.pk, inquiry.last_error)
        return False
    logger.info("문의 %s: 전달 완료, 대기열에서 지움", inquiry.pk)
    inquiry.delete()
    return True


def resend_pending(now: datetime) -> tuple[int, int]:
    """대기 건을 모두 다시 보낸다. (보내서 지운 수, 아직 대기인 수)를 돌려준다."""
    sent = 0
    for inquiry in Inquiry.objects.filter(status=Inquiry.Status.PENDING):
        if deliver(inquiry, now):
            sent += 1
    return sent, Inquiry.objects.filter(status=Inquiry.Status.PENDING).count()
