from django.core.exceptions import ValidationError
from django.db import models

from services.html_rules import HtmlRulesError, validate_html_rules


class Source(models.Model):
    """뉴스를 가져오는 출처 하나(GLOSSARY: 소스)."""

    class Kind(models.TextChoices):
        RSS = "rss", "RSS"
        HTML = "html", "HTML"

    name = models.CharField("이름", max_length=100)
    kind = models.CharField("종류", max_length=10, choices=Kind.choices, default=Kind.RSS)
    url = models.URLField("주소", max_length=2000)
    adapter_name = models.CharField(
        "어댑터 이름", max_length=50, default="rss", help_text="adapters.sources.ADAPTERS의 키"
    )
    html_rules = models.JSONField(
        "HTML 선택자 규칙",
        default=dict,
        blank=True,
        help_text="kind=html일 때 필수. 키: item, link, title, published, (선택) excerpt.",
    )
    enabled = models.BooleanField("켜짐", default=True)
    last_collected_at = models.DateTimeField("마지막 수집 시각", null=True, blank=True)
    last_result = models.TextField("마지막 수집 결과", blank=True)
    last_succeeded = models.BooleanField("마지막 수집 성공", null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField("연속 실패 횟수", default=0)

    class Meta:
        verbose_name = "소스"
        verbose_name_plural = "소스"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.kind != self.Kind.HTML:
            return
        if self.adapter_name == "rss":
            self.adapter_name = "html"
        try:
            self.html_rules = validate_html_rules(self.html_rules)
        except HtmlRulesError as exc:
            raise ValidationError({"html_rules": str(exc)}) from exc


class Item(models.Model):
    """크롤러가 소스에서 가져온 원재료 하나(GLOSSARY: 수집물)."""

    class Status(models.TextChoices):
        PENDING = "pending", "미처리"
        BUNDLED = "bundled", "묶임"
        DISCARDED = "discarded", "버림"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="items")
    url = models.URLField("원문 URL", max_length=2000)
    normalized_url = models.URLField("정규화 URL", max_length=2000)
    title = models.CharField("제목", max_length=500)
    excerpt = models.TextField("본문 발췌", blank=True)
    published_at = models.DateTimeField("발행 시각", null=True, blank=True)
    collected_at = models.DateTimeField("수집 시각", auto_now_add=True)
    status = models.CharField(
        "상태", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    class Meta:
        verbose_name = "수집물"
        verbose_name_plural = "수집물"
        ordering = ["-collected_at", "-id"]

    def __str__(self) -> str:
        return self.title


class SeenUrl(models.Model):
    """정규화 URL의 SHA-256 해시.

    수집물이 지워져도 남아 재수집을 막는다(#5 정리 명령은 지우지 않는다).
    """

    url_hash = models.CharField(max_length=64, unique=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "본 URL"
        verbose_name_plural = "본 URL"

    def __str__(self) -> str:
        return self.url_hash
