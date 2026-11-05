from django.core.exceptions import ValidationError
from django.db import models

from services.filter_rules import FilterRulesError, validate_filter_rules
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
        help_text="kind=html일 때 필수. 키: item, link, title, published, (선택) excerpt, image.",
    )
    filter_rules = models.JSONField(
        "거르기 규칙",
        default=dict,
        db_default={},
        blank=True,
        help_text="키(모두 선택, 문자열 목록, 대소문자 구분 부분 문자열): exclude_url, "
        "exclude_title, include_any(제목+발췌 앞 300자에 하나도 없으면 거름).",
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
        try:
            self.filter_rules = validate_filter_rules(self.filter_rules)
        except FilterRulesError as exc:
            raise ValidationError({"filter_rules": str(exc)}) from exc
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
        FILTERED = "filtered", "걸러냄"

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="items")
    url = models.URLField("원문 URL", max_length=2000)
    normalized_url = models.URLField("정규화 URL", max_length=2000)
    title = models.CharField("제목", max_length=500)
    excerpt = models.TextField("본문 발췌", blank=True)
    published_at = models.DateTimeField("발행 시각", null=True, blank=True)
    image_url = models.URLField("대표 이미지 URL", max_length=2000, blank=True)
    collected_at = models.DateTimeField("수집 시각", auto_now_add=True)
    status = models.CharField(
        "상태", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    filter_reason = models.CharField(
        "거른 사유", max_length=200, blank=True, default="", db_default=""
    )

    class AiVerdict(models.TextChoices):
        NONE = "", "판별 안 함"
        IN = "in", "주제 안"
        OUT = "out", "주제 밖"
        ERROR = "error", "판별 실패"

    ai_verdict = models.CharField(
        "AI 판별",
        max_length=5,
        choices=AiVerdict.choices,
        blank=True,
        default="",
        db_default="",
    )
    ai_reason = models.CharField(
        "AI 판별 사유", max_length=200, blank=True, default="", db_default=""
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


class FilterCall(models.Model):
    """수집 AI 판별(#160) 요청 하나의 비용 기록. 비용은 편집 월 예산 합계에 더한다."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "성공"
        FAILED = "failed", "실패"

    created_at = models.DateTimeField("시각", auto_now_add=True, db_index=True)
    source = models.ForeignKey(
        Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="filter_calls"
    )
    mode = models.CharField("모드", max_length=10)
    model = models.CharField("모델", max_length=60)
    status = models.CharField("상태", max_length=10, choices=Status.choices)
    items = models.PositiveIntegerField("판별 대상 수", default=0)
    out_count = models.PositiveIntegerField("밖 판정 수", default=0)
    input_tokens = models.PositiveIntegerField("입력 토큰", default=0)
    output_tokens = models.PositiveIntegerField("출력 토큰", default=0)
    cost_usd = models.DecimalField("추정 비용(US$)", max_digits=10, decimal_places=6, default=0)
    error = models.CharField("오류", max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "수집 AI 판별 호출"
        verbose_name_plural = "수집 AI 판별 호출"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.model} {self.items}건"
