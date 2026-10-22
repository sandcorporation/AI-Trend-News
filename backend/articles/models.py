from django.db import models

from collection.models import Item


class Category(models.Model):
    """기사가 속하는 뉴스 분야 하나(GLOSSARY: 분야)."""

    name = models.CharField("이름", max_length=50, unique=True)
    slug = models.SlugField("슬러그", max_length=60, unique=True, editable=False)
    order = models.PositiveIntegerField("순서", default=0)

    class Meta:
        verbose_name = "분야"
        verbose_name_plural = "분야"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name


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
    slug = models.SlugField("슬러그", max_length=80, unique=True, editable=False)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="articles", verbose_name="분야"
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
