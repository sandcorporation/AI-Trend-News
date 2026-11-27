from datetime import timedelta
from typing import Any

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet, Sum
from django.forms import BaseModelFormSet, ModelForm
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.functional import cached_property

from adapters.clock import get_clock
from articles.models import (
    Article,
    ArticleItem,
    Category,
    Commentary,
    OutboundClick,
    Prediction,
    Tag,
)
from services import articles as svc
from services import commentary as commentary_svc
from services import outbound
from services import predictions as predictions_svc

_TEXT_FIELDS = ("background", "significance", "outlook_bull", "outlook_bear", "opinion")
_JSON_FIELDS = ("predictions", "watchpoints")


class ArticleItemInline(admin.TabularInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    model = ArticleItem
    extra = 0
    fields = ("item", "item_status", "item_url")
    readonly_fields = ("item", "item_status", "item_url")

    @admin.display(description="상태")
    def item_status(self, obj: ArticleItem) -> str:
        return obj.item.get_status_display()

    @admin.display(description="원문")
    def item_url(self, obj: ArticleItem) -> str:
        return obj.item.url

    def has_add_permission(self, request: HttpRequest, obj: Article | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: ArticleItem | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ArticleItem | None = None) -> bool:
        return False


class CommentaryForm(ModelForm):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """고친 분석 칸은 편집자 경로와 같은 검사를 거치고, 오류는 그 칸에 붙인다. 새 분석은 모두 검사.

    손대지 않은 칸은 검사하지 않는다(검사가 생기기 전 값이 남아도 다른 칸을 고치고 숨길 수 있게).
    """

    class Meta:
        model = Commentary
        fields = (
            "background",
            "significance",
            "outlook_bull",
            "outlook_bear",
            "opinion",
            "predictions",
            "watchpoints",
            "hidden",
        )

    @cached_property
    def changed_data(self) -> list[str]:
        """textarea는 CRLF로 보내서, 손대지 않은 여러 문단 칸이 바뀐 것으로 잡힌다. 그것은 뺀다."""
        return [
            name
            for name in super().changed_data
            if name not in _TEXT_FIELDS
            or svc.is_text_changed(
                str(self.initial.get(name, "")), str(self.data.get(self.add_prefix(name), ""))
            )
        ]

    def clean(self) -> dict[str, Any]:
        data: dict[str, Any] = super().clean() or {}
        adding = self.instance.pk is None
        for name in _TEXT_FIELDS:
            if not adding and name not in self.changed_data and name in data:
                data[name] = self.initial[name]  # 줄바꿈 표기만 다른 값은 저장된 그대로 둔다
        written = self.instance.created_at or get_clock().now()
        created = timezone.localtime(written).date()
        for name in (*_TEXT_FIELDS, *_JSON_FIELDS):
            # 칸 자체의 오류(빈 칸·JSON 문법)가 있는 칸은 `data`에 없고 그 오류만 보인다.
            if name not in data or not (adding or name in self.changed_data):
                continue
            try:
                if name in _JSON_FIELDS:
                    data[name] = commentary_svc.clean_json_field(name, data[name], created=created)
                else:
                    data[name] = commentary_svc.clean_text(name, data[name])
            except commentary_svc.CommentaryError as error:
                self.add_error(name, str(error))
        return data


class CommentaryInline(admin.StackedInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """대표가 분석을 고치거나 숨긴다(편집자는 서비스로만 쓴다)."""

    model = Commentary
    form = CommentaryForm
    extra = 0
    max_num = 1
    can_delete = True


class ArticleForm(ModelForm):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    def clean_slug(self) -> str:
        """바꾸거나 새로 정한 slug는 서비스와 같은 검사를 거친다. 비우면 자동·유지."""
        slug: str = self.cleaned_data["slug"]
        if not slug or "slug" not in self.changed_data:
            return slug
        try:
            return svc.check_article_slug(slug, exclude=self.instance if self.instance.pk else None)
        except svc.ArticleError as error:
            raise ValidationError(str(error)) from None


class CategoryForm(ModelForm):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    def clean_slug(self) -> str:
        slug: str = self.cleaned_data["slug"]
        if not slug or "slug" not in self.changed_data:
            return slug
        try:
            return svc.check_category_slug(
                slug, exclude=self.instance if self.instance.pk else None
            )
        except svc.ArticleError as error:
            raise ValidationError(str(error)) from None


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    form = ArticleForm
    list_display = (
        "title",
        "category",
        "published",
        "featured",
        "has_commentary",
        "views_7d",
        "published_at",
    )
    list_filter = ("category", "published", "featured")
    search_fields = ("title", "summary")
    readonly_fields = (
        "old_slugs",
        "title_overridden",
        "summary_overridden",
        "published_overridden",
        "featured_overridden",
        "published_at_overridden",
    )

    @admin.display(description="옛 슬러그")
    def old_slugs(self, obj: Article) -> str:
        return ", ".join(a.slug for a in obj.old_slugs.all()) or "-"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
        since = timezone.localdate() - timedelta(days=6)
        qs: QuerySet[Article] = super().get_queryset(request)
        return qs.select_related("commentary").annotate(
            views_7d_sum=Sum("views__count", filter=Q(views__date__gte=since))
        )

    @admin.display(description="분석 있음", boolean=True)
    def has_commentary(self, obj: Article) -> bool:
        return hasattr(obj, "commentary")

    @admin.display(description="7일 조회", ordering="views_7d_sum")
    def views_7d(self, obj: Article) -> int:
        return getattr(obj, "views_7d_sum", None) or 0

    inlines = [ArticleItemInline, CommentaryInline]

    def save_model(
        self,
        request: HttpRequest,
        obj: Article,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        """admin은 대표의 도구다. 바꾼 제목·요약·발행 시각·플래그는 덮어씀을 표시한다."""
        if not change:
            obj.title_overridden = obj.summary_overridden = True
            if not obj.slug:
                obj.slug = svc.make_slug(obj.title, Article, "news")
            obj.save()
            return
        published, featured = obj.published, obj.featured
        stored = Article.objects.get(pk=obj.pk)
        obj.published, obj.featured = stored.published, stored.featured
        if svc.is_text_changed(stored.title, obj.title):
            obj.title_overridden = True
        if svc.is_text_changed(stored.summary, obj.summary):
            obj.summary_overridden = True
        if obj.published_at != stored.published_at:
            obj.published_at_overridden = True
        if not obj.slug:
            obj.slug = stored.slug
        elif obj.slug != stored.slug:
            new = obj.slug
            obj.slug = stored.slug
            svc.record_article_slug_change(obj, new)
        obj.save()
        svc.set_flags(svc.Actor.OWNER, obj.pk, published=published, featured=featured)
        obj.refresh_from_db()

    def save_formset(
        self,
        request: HttpRequest,
        form: ModelForm,  # type: ignore[type-arg]
        formset: BaseModelFormSet,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        super().save_formset(request, form, formset, change)
        if formset.model is not Commentary:
            return
        saved = [
            *formset.new_objects,
            *(obj for obj, changed in formset.changed_objects if "predictions" in changed),
        ]
        for commentary in saved:
            predictions_svc.sync_predictions(commentary)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Article]) -> None:
        for article in queryset:
            article.delete()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    form = CategoryForm
    list_display = ("name", "slug", "order")
    list_editable = ("order",)

    @admin.display(description="옛 슬러그")
    def old_slugs(self, obj: Category) -> str:
        return ", ".join(a.slug for a in obj.old_slugs.all()) or "-"

    readonly_fields = ("old_slugs",)

    def delete_model(self, request: HttpRequest, obj: Category) -> None:
        svc.delete_category(obj.slug, confirm=True)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Category]) -> None:
        for category in queryset:
            svc.delete_category(category.slug, confirm=True)

    def save_model(
        self,
        request: HttpRequest,
        obj: Category,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        if not obj.slug:
            obj.slug = svc.make_slug(obj.name, Category, "category")
        elif change:
            old = Category.objects.get(pk=obj.pk).slug
            if old != obj.slug:
                new = obj.slug
                obj.slug = old
                svc.record_slug_change(obj, new)
        super().save_model(request, obj, form, change)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    search_fields = ("name",)


@admin.register(OutboundClick)
class OutboundClickAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """유입 링크 클릭. 위에 기간(오늘·7일·30일)별 위치·기사 집계를 보인다. 기록은 읽기 전용."""

    change_list_template = "admin/articles/outboundclick/change_list.html"
    list_display = ("clicked_at", "location", "article", "referrer")
    list_filter = ("location", "clicked_at")

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> TemplateResponse:
        context = {**(extra_context or {}), "summaries": outbound.summarize()}
        return super().changelist_view(request, context)  # type: ignore[return-value]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: OutboundClick | None = None) -> bool:
        return False


@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """대표가 예측의 결과를 매긴다(#52). 문구는 편집자가 쓰므로 결과·메모만 고친다."""

    list_display = ("claim", "article_title", "due_date", "verdict", "judged_at")
    list_filter = ("verdict", "horizon")
    ordering = ("due_date", "id")
    fields = ("claim", "horizon", "basis", "due_date", "verdict", "verdict_note", "judged_at")
    readonly_fields = ("claim", "horizon", "basis", "due_date", "judged_at")
    actions = ["mark_hit", "mark_miss", "mark_undecidable", "mark_pending"]

    @admin.display(description="기사")
    def article_title(self, obj: Prediction) -> str:
        return obj.commentary.article.title

    def get_queryset(self, request: HttpRequest) -> QuerySet[Prediction]:
        qs: QuerySet[Prediction] = super().get_queryset(request)
        return qs.select_related("commentary__article")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def save_model(
        self,
        request: HttpRequest,
        obj: Prediction,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        predictions_svc.judge_prediction(
            obj.pk, obj.verdict, obj.verdict_note, now=get_clock().now()
        )

    def _judge(self, request: HttpRequest, queryset: QuerySet[Prediction], verdict: str) -> None:
        rows = list(queryset)
        for row in rows:
            predictions_svc.judge_prediction(
                row.pk, verdict, row.verdict_note, now=get_clock().now()
            )
        label = Prediction.Verdict(verdict).label
        self.message_user(
            request, f"예측 {len(rows)}건을 「{label}」 상태로 바꿨습니다.", messages.SUCCESS
        )

    @admin.action(description="적중으로 표시", permissions=["change"])
    def mark_hit(self, request: HttpRequest, queryset: QuerySet[Prediction]) -> None:
        self._judge(request, queryset, Prediction.Verdict.HIT)

    @admin.action(description="빗나감으로 표시", permissions=["change"])
    def mark_miss(self, request: HttpRequest, queryset: QuerySet[Prediction]) -> None:
        self._judge(request, queryset, Prediction.Verdict.MISS)

    @admin.action(description="판단 불가로 표시", permissions=["change"])
    def mark_undecidable(self, request: HttpRequest, queryset: QuerySet[Prediction]) -> None:
        self._judge(request, queryset, Prediction.Verdict.UNDECIDABLE)

    @admin.action(description="미평가로 되돌림", permissions=["change"])
    def mark_pending(self, request: HttpRequest, queryset: QuerySet[Prediction]) -> None:
        self._judge(request, queryset, Prediction.Verdict.PENDING)
