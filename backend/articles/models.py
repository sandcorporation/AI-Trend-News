import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from collection.models import Item

CATEGORY_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ARTICLE_SLUG_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
article_slug_validator = RegexValidator(
    re.compile(rf"\A{ARTICLE_SLUG_RE.pattern}\Z"),
    "라틴 소문자·숫자·하이픈만 쓰고 첫 글자는 소문자여야 합니다.",
)
category_slug_validator = RegexValidator(
    re.compile(rf"\A{CATEGORY_SLUG_RE.pattern}\Z"), "라틴 소문자·숫자·하이픈만 쓸 수 있습니다."
)


class Category(models.Model):
    """기사가 속하는 뉴스 분야 하나(GLOSSARY: 분야)."""

    name = models.CharField("이름", max_length=50, unique=True)
    slug = models.SlugField(
        "슬러그",
        max_length=60,
        unique=True,
        blank=True,
        validators=[category_slug_validator],
        help_text="공개 주소 ?category= 값(라틴 소문자·숫자·하이픈). 비우면 자동 생성.",
    )
    order = models.PositiveIntegerField("순서", default=0)

    class Meta:
        verbose_name = "분야"
        verbose_name_plural = "분야"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if (
            self.slug
            and CategorySlugAlias.objects.filter(slug=self.slug)
            .exclude(category_id=self.pk)
            .exists()
        ):
            raise ValidationError({"slug": "다른 분야가 예전에 쓰던 slug입니다."})


class CategorySlugAlias(models.Model):
    """분야가 예전에 쓰던 slug. 옛 ?category= 링크를 새 slug로 301 보내는 데 쓴다."""

    slug = models.SlugField("옛 슬러그", max_length=60, unique=True)
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="old_slugs", verbose_name="분야"
    )

    class Meta:
        verbose_name = "분야 옛 슬러그"
        verbose_name_plural = "분야 옛 슬러그"

    def __str__(self) -> str:
        return f"{self.slug} -> {self.category.slug}"


class Tag(models.Model):
    """기사에 붙이는 자유 키워드(GLOSSARY: 태그)."""

    name = models.CharField("이름", max_length=50, unique=True)

    class Meta:
        verbose_name = "태그"
        verbose_name_plural = "태그"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Article(models.Model):
    """수집물을 묶어 만든 한국어 기사(GLOSSARY: 기사)."""

    title = models.CharField("제목", max_length=300)
    summary = models.TextField("요약", blank=True)
    slug = models.SlugField(
        "슬러그",
        max_length=80,
        unique=True,
        blank=True,
        validators=[article_slug_validator],
        help_text="/a/ 주소(라틴 소문자·숫자·하이픈). 비우면 자동. 바꾸면 옛 주소 301.",
    )
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="articles", verbose_name="분야"
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="articles", verbose_name="태그")
    published = models.BooleanField("게시", default=False)
    featured = models.BooleanField("추천", default=False)
    published_overridden = models.BooleanField("게시: 대표 덮어씀", default=False, editable=False)
    featured_overridden = models.BooleanField("추천: 대표 덮어씀", default=False, editable=False)
    image_url = models.URLField("대표 이미지 URL", max_length=2000, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        verbose_name = "기사"
        verbose_name_plural = "기사"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        if (
            self.slug
            and ArticleSlugAlias.objects.filter(slug=self.slug).exclude(article_id=self.pk).exists()
        ):
            raise ValidationError({"slug": "다른 기사가 예전에 쓰던 slug입니다."})


class ArticleSlugAlias(models.Model):
    """기사가 예전에 쓰던 slug. 옛 /a/<slug> 링크를 새 slug로 301 보내는 데 쓴다."""

    slug = models.SlugField("옛 슬러그", max_length=80, unique=True)
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="old_slugs", verbose_name="기사"
    )

    class Meta:
        verbose_name = "기사 옛 슬러그"
        verbose_name_plural = "기사 옛 슬러그"

    def __str__(self) -> str:
        return f"{self.slug} -> {self.article.slug}"


class ArticleItem(models.Model):
    """기사-수집물 묶음. 수집물은 기사 하나에만(OneToOne).

    기사를 지우면 이 행만 지워진다. 수집물 삭제는 이 행을 지우지만 그 반대는 없다.
    """

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="article_items")
    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="article_link")

    class Meta:
        verbose_name = "묶인 수집물"
        verbose_name_plural = "묶인 수집물"

    def __str__(self) -> str:
        return f"{self.article_id} - {self.item_id}"


class OutboundClick(models.Model):
    """유입 링크 클릭 한 번(GLOSSARY: 유입 링크). 봇은 기록하지 않는다."""

    class Location(models.TextChoices):
        HEADER = "header", "헤더"
        FOOTER = "footer", "푸터"
        ARTICLE_CTA = "article_cta", "기사 하단 CTA"
        CONTACT = "contact", "문의"
        SIDEBAR = "sidebar", "사이드바"

    clicked_at = models.DateTimeField("시각", auto_now_add=True, db_index=True)
    location = models.CharField("위치", max_length=20, choices=Location.choices)
    article = models.ForeignKey(
        Article,
        verbose_name="기사",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="outbound_clicks",
    )
    referrer = models.CharField("referrer", max_length=500, blank=True)
    user_agent = models.CharField("user agent", max_length=200, blank=True)

    class Meta:
        verbose_name = "유입 링크 클릭"
        verbose_name_plural = "유입 링크 클릭"
        ordering = ["-clicked_at", "-id"]

    def __str__(self) -> str:
        return f"{self.location} @ {self.clicked_at:%Y-%m-%d %H:%M}"


class ArticleView(models.Model):
    """기사 하루치 조회 수(GLOSSARY: 조회). 개인 식별 정보는 저장하지 않는다."""

    article = models.ForeignKey(
        Article, verbose_name="기사", on_delete=models.CASCADE, related_name="views"
    )
    date = models.DateField("날짜", db_index=True)
    count = models.PositiveIntegerField("조회 수", default=0)

    class Meta:
        verbose_name = "기사 조회"
        verbose_name_plural = "기사 조회"
        constraints = [
            models.UniqueConstraint(fields=["article", "date"], name="articleview_article_date")
        ]

    def __str__(self) -> str:
        return f"{self.article_id} {self.date} {self.count}"


class Commentary(models.Model):
    """추천 기사에 편집자가 쓰는 「AI 분석」(GLOSSARY: 분석). 기사당 최대 하나."""

    article = models.OneToOneField(
        Article, verbose_name="기사", on_delete=models.CASCADE, related_name="commentary"
    )
    background = models.TextField("배경")
    significance = models.TextField("의미")
    outlook_bull = models.TextField("전망: 낙관")
    outlook_bear = models.TextField("전망: 회의")
    opinion = models.TextField("의견")
    predictions = models.JSONField(
        "예측 문장",
        default=list,
        blank=True,
        help_text='0~3개 {"claim": 문장, "horizon": 1m|3m|6m|12m, "basis": 근거}',
    )
    hidden = models.BooleanField(
        "숨김", default=False, help_text="대표가 숨기면 편집자가 못 덮어쓴다"
    )
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        verbose_name = "AI 분석"
        verbose_name_plural = "AI 분석"

    def __str__(self) -> str:
        return f"분석 · {self.article.title}"
