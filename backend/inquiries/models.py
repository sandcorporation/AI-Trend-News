from django.db import models


class Inquiry(models.Model):
    """portfolio로 전달하기 전/실패한 문의 한 건(GLOSSARY: 문의). 보낸 건은 바로 지운다(ADR 0003).

    개인정보라 로그에 내용·연락처를 남기지 않는다.
    """

    class Kind(models.TextChoices):
        INQUIRY = "문의", "문의"
        PROPOSAL = "제안", "제안"

    class Status(models.TextChoices):
        PENDING = "pending", "대기"
        SENT = "sent", "보냄"
        FAILED = "failed", "영구실패"

    name = models.CharField("이름", max_length=50)
    company = models.CharField("회사", max_length=100, blank=True)
    contact = models.CharField("연락처", max_length=200)
    message = models.TextField("내용", max_length=5000)
    kind = models.CharField("종류", max_length=10, choices=Kind.choices, default=Kind.INQUIRY)
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField("시도 횟수", default=0)
    last_error = models.CharField("마지막 오류", max_length=200, blank=True)
    sent_at = models.DateTimeField("보낸 시각", null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        verbose_name = "문의 대기열"
        verbose_name_plural = "문의 대기열"
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"문의 {self.pk} ({self.get_status_display()})"


class SubmitAttempt(models.Model):
    """IP별 요청 제한용 기록. 워커끼리 공유하려고 DB에 둔다. 오래된 것은 제출 때 지운다."""

    ip = models.CharField(max_length=64)
    at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["ip", "at"])]

    def __str__(self) -> str:
        return f"{self.ip} {self.at:%H:%M}"
