from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from posts.models import Post
from services import posts as svc
from services.articles import Actor


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """초안 목록과 복사 화면. 문구는 편집자가 쓰므로 여기서는 읽기 전용이고, 올림 표시만 한다."""

    change_form_template = "admin/posts/post/change_form.html"
    list_display = ("article", "channel", "status", "posted_at", "updated_at")
    list_filter = ("status", "channel")
    fields = ("article", "channel", "status", "text", "posted_at", "created_at", "updated_at")
    readonly_fields = fields
    actions = ["mark_posted"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Post | None = None) -> bool:
        return False

    def has_view_permission(self, request: HttpRequest, obj: Post | None = None) -> bool:
        return True

    @admin.action(description="올림으로 표시")
    def mark_posted(self, request: HttpRequest, queryset: QuerySet[Post]) -> None:
        for post in queryset:
            svc.mark_post_posted(Actor.OWNER, post.pk)
        self.message_user(
            request, f"{queryset.count()}건을 올림으로 표시했습니다.", messages.SUCCESS
        )
