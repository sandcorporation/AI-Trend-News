from django.db import models

from articles.models import Article


class Post(models.Model):
    """추천 기사를 채널에 올릴 문구 한 건(GLOSSARY: 포스트). 기사·채널당 하나."""

    class Channel(models.TextChoices):
        KAKAO = "kakao", "카카오톡"

    class Status(models.TextChoices):
        DRAFT = "draft", "초안"
        POSTED = "posted", "올림"

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="posts")
    channel = models.CharField("채널", max_length=20, choices=Channel.choices)
    text = models.TextField("문구")
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField("올린 시각", null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        verbose_name = "포스트"
        verbose_name_plural = "포스트"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["article", "channel"], name="post_one_per_channel")
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} · {self.article.title}"
