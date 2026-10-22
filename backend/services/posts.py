"""포스트 초안 규칙(#14). 편집자는 초안을 쓰고, 「올림」 표시는 대표만 한다.

채널을 더하려면 `Post.Channel`에 선택지를 추가하고(마이그레이션) 필요하면 채널별 길이 규칙을
`MAX_TEXT_LENGTH`에 둔다. 전송 연동은 없다(대표가 admin에서 복사해 올린다).
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from posts.models import Post
from services import articles
from services.articles import Actor

MAX_TEXT_LENGTH = 500


class PostError(Exception):
    """포스트 규칙 위반의 공통 부모."""


class PostNotFound(PostError):
    pass


class UnknownChannel(PostError):
    pass


class ArticleNotPostable(PostError):
    pass


class PostAlreadyPosted(PostError):
    pass


class InvalidPostText(PostError):
    pass


class OwnerOnly(PostError):
    pass


def article_url(slug: str) -> str:
    return f"{settings.SITE_URL}/a/{slug}"


@transaction.atomic
def upsert_post_draft(actor: Actor, article_id: int, channel: str, text: str) -> Post:
    """기사·채널당 초안 하나를 만들거나 갱신한다. 추천이면서 게시된 기사만, 올림이면 수정 불가."""
    article = articles.get_article(article_id)
    if channel not in Post.Channel.values:
        raise UnknownChannel(
            f"모르는 채널입니다: {channel} (있는 채널: {', '.join(Post.Channel.values)})"
        )
    if not (article.featured and article.published):
        raise ArticleNotPostable(
            f"추천이면서 게시된 기사에만 포스트를 쓸 수 있습니다: {article_id} "
            f"(게시 {article.published}, 추천 {article.featured})"
        )
    text = text.strip()
    if not text or len(text) > MAX_TEXT_LENGTH:
        raise InvalidPostText(f"문구는 1~{MAX_TEXT_LENGTH}자여야 합니다(지금 {len(text)}자).")
    link = article_url(article.slug)
    if link not in text:
        raise InvalidPostText(f"문구에 기사 링크({link})가 들어 있어야 합니다.")

    existing = Post.objects.select_for_update().filter(article=article, channel=channel).first()
    if existing is None:
        return Post.objects.create(article=article, channel=channel, text=text)
    if existing.status == Post.Status.POSTED and actor is not Actor.OWNER:
        raise PostAlreadyPosted(f"이미 올린 포스트라 고칠 수 없습니다: {existing.pk}")
    existing.text = text
    existing.save(update_fields=["text", "updated_at"])
    return existing


@transaction.atomic
def mark_post_posted(actor: Actor, post_id: int) -> Post:
    """대표만: 올림으로 표시하고 시각을 남긴다. 이미 올림이면 시각을 바꾸지 않는다."""
    if actor is not Actor.OWNER:
        raise OwnerOnly("올림 표시는 대표만 할 수 있습니다.")
    try:
        post = Post.objects.select_for_update().get(pk=post_id)
    except Post.DoesNotExist:
        raise PostNotFound(f"없는 포스트입니다: {post_id}") from None
    if post.status != Post.Status.POSTED:
        post.status = Post.Status.POSTED
        post.posted_at = timezone.now()
        post.save(update_fields=["status", "posted_at", "updated_at"])
    return post
