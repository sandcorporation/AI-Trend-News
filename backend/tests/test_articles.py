from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from articles.models import Article, Category
from collection.models import Item, SeenUrl, Source
from services import articles as svc
from services.articles import Actor

pytestmark = pytest.mark.django_db


def make_items(n: int = 2) -> list[Item]:
    source = Source.objects.create(name="s", url="https://example.com/feed")
    return [
        Item.objects.create(
            source=source,
            url=f"https://example.com/{i}",
            normalized_url=f"https://example.com/{i}",
            title=f"t{i}",
        )
        for i in range(n)
    ]


def make_article(items: list[Item], **kw: object) -> Article:
    svc.add_category("경제")
    args: dict[str, object] = {
        "item_ids": [i.pk for i in items],
        "title": "금리 동결",
        "summary": "요약",
        "category": svc.list_categories()[0].slug,
    }
    args.update(kw)
    return svc.create_article(Actor.EDITOR, **args)  # type: ignore[arg-type]


def test_create_article_bundles_both_items() -> None:
    items = make_items(2)
    article = make_article(items, tags=["금리", "금리", " 은행 "])
    assert {i.status for i in Item.objects.all()} == {Item.Status.BUNDLED}
    assert {a.item_id for a in article.article_items.all()} == {i.pk for i in items}
    assert sorted(t.name for t in article.tags.all()) == ["금리", "은행"]


def test_rebinding_error_names_the_article() -> None:
    items = make_items(2)
    article = make_article(items[:1])
    with pytest.raises(svc.ItemAlreadyBundled) as exc:
        svc.create_article(
            Actor.EDITOR,
            item_ids=[items[0].pk],
            title="다른",
            summary="",
            category=article.category.slug,
        )
    assert str(article.pk) in str(exc.value) and "금리 동결" in str(exc.value)
    assert Article.objects.count() == 1


def test_update_adds_items_and_keeps_slug() -> None:
    items = make_items(2)
    article = make_article(items[:1])
    slug = article.slug
    updated = svc.update_article(
        Actor.EDITOR, article.pk, add_item_ids=[items[1].pk], title="새 제목", tags=["x"]
    )
    assert updated.slug == slug and updated.title == "새 제목"
    assert updated.article_items.count() == 2


def test_unknown_category_is_an_error() -> None:
    items = make_items(1)
    with pytest.raises(svc.CategoryNotFound, match="nope"):
        svc.create_article(
            Actor.EDITOR, item_ids=[items[0].pk], title="a", summary="", category="nope"
        )
    assert Item.objects.get().status == Item.Status.PENDING


def test_slug_for_korean_titles_is_unique() -> None:
    items = make_items(2)
    a = make_article(items[:1])
    b = svc.create_article(
        Actor.EDITOR,
        item_ids=[items[1].pk],
        title="금리 동결",
        summary="",
        category=a.category.slug,
    )
    assert a.slug != b.slug and a.slug.startswith("news-")


def test_owner_override_blocks_editor() -> None:
    article = make_article(make_items(1), published=True)
    svc.set_flags(Actor.OWNER, article.pk, published=False)
    article.refresh_from_db()
    assert article.published_overridden and not article.featured_overridden
    with pytest.raises(svc.FlagOverridden):
        svc.set_flags(Actor.EDITOR, article.pk, published=True)
    article.refresh_from_db()
    assert not article.published
    # 덮어쓰지 않은 플래그는 편집자가 바꿀 수 있다
    assert svc.set_flags(Actor.EDITOR, article.pk, featured=True).featured


def test_discard_item() -> None:
    items = make_items(2)
    make_article(items[:1])
    assert svc.discard_item(Actor.EDITOR, items[1].pk).status == Item.Status.DISCARDED
    with pytest.raises(svc.ItemAlreadyBundled):
        svc.discard_item(Actor.EDITOR, items[0].pk)


def test_deleting_article_keeps_items_and_seen_urls() -> None:
    items = make_items(2)
    SeenUrl.objects.create(url_hash="a" * 64)
    article = make_article(items)
    svc.delete_article(article.pk)
    assert Item.objects.count() == 2
    assert {i.status for i in Item.objects.all()} == {Item.Status.PENDING}
    assert SeenUrl.objects.count() == 1


def test_admin_flag_change_marks_override_and_blocks_editor(admin_client: Client) -> None:
    article = make_article(make_items(1), published=True)
    url = reverse("admin:articles_article_change", args=[article.pk])
    data = {
        "title": article.title,
        "summary": article.summary,
        "category": article.category.pk,
        "image_url": "",
        "published_at_0": "2026-01-01",
        "published_at_1": "00:00:00",
        "featured": "",
        "article_items-TOTAL_FORMS": "0",
        "article_items-INITIAL_FORMS": "0",
        "commentary-TOTAL_FORMS": "0",
        "commentary-INITIAL_FORMS": "0",
    }
    assert admin_client.post(url, data).status_code == 302
    article.refresh_from_db()
    assert not article.published and article.published_overridden
    with pytest.raises(svc.FlagOverridden):
        svc.set_flags(Actor.EDITOR, article.pk, published=True)
    page = admin_client.get(url)
    assert page.status_code == 200


def test_admin_deleting_article_leaves_items(admin_client: Client) -> None:
    items = make_items(2)
    article = make_article(items)
    url = reverse("admin:articles_article_delete", args=[article.pk])
    assert admin_client.post(url, {"post": "yes"}).status_code == 302
    assert Article.objects.count() == 0
    assert Item.objects.filter(status=Item.Status.PENDING).count() == 2


def test_admin_shows_bundled_items_inline(admin_client: Client) -> None:
    article = make_article(make_items(2))
    resp = admin_client.get(reverse("admin:articles_article_change", args=[article.pk]))
    assert b"https://example.com/0" in resp.content and "묶임".encode() in resp.content


def test_admin_adds_category_with_slug(admin_client: Client) -> None:
    resp = admin_client.post(reverse("admin:articles_category_add"), {"name": "정치", "order": 3})
    assert resp.status_code == 302
    assert Category.objects.get(name="정치").slug.startswith("category-")


def test_add_category_with_slug_and_validation() -> None:
    assert svc.add_category("경제", slug="economy").slug == "economy"
    assert svc.add_category("정치").slug.startswith("category-")
    with pytest.raises(svc.CategoryExists):
        svc.add_category("사회", slug="economy")
    for bad in ("경제", "Economy", "a_b", "-a", "a--b", "a" * 61):
        with pytest.raises(svc.InvalidCategorySlug):
            svc.add_category(f"x{bad}", slug=bad)


def test_update_category_slug_keeps_old_slug_and_blocks_reuse() -> None:
    svc.add_category("경제", slug="old")
    svc.add_category("정치", slug="politics")
    assert svc.update_category("old", new_slug="economy").slug == "economy"
    assert svc.find_category_by_old_slug("old").slug == "economy"  # type: ignore[union-attr]
    with pytest.raises(svc.CategoryExists):
        svc.update_category("politics", new_slug="old")
    with pytest.raises(svc.CategoryExists):
        svc.add_category("세계", slug="old")
    svc.update_category("economy", new_slug="old")  # 되돌리면 옛 기록이 현재 slug가 된다
    assert svc.find_category_by_old_slug("old") is None
    assert svc.find_category_by_old_slug("economy").slug == "old"  # type: ignore[union-attr]


def test_admin_form_saves_slug_and_records_old() -> None:
    from django.contrib import admin as dj_admin
    from django.forms import modelform_factory
    from django.test import RequestFactory

    from articles.admin import CategoryAdmin
    from articles.models import Category

    ma = CategoryAdmin(Category, dj_admin.site)
    form_cls = modelform_factory(Category, fields=("name", "slug", "order"))
    blank = form_cls({"name": "경제", "slug": "", "order": 0})
    assert blank.is_valid()
    obj = blank.save(commit=False)
    ma.save_model(RequestFactory().post("/"), obj, blank, False)
    assert obj.slug.startswith("category-")
    old = obj.slug
    form = form_cls(
        {"name": "경제", "slug": "economy", "order": 0}, instance=Category.objects.get(pk=obj.pk)
    )
    assert form.is_valid()
    changed = form.save(commit=False)
    ma.save_model(RequestFactory().post("/"), changed, form, True)
    assert Category.objects.get(pk=obj.pk).slug == "economy"
    assert svc.find_category_by_old_slug(old).slug == "economy"  # type: ignore[union-attr]
    bad = form_cls({"name": "정치", "slug": "Bad Slug", "order": 1})
    assert not bad.is_valid()
    other = form_cls({"name": "정치", "slug": old, "order": 1})
    assert not other.is_valid()


def test_editor_slug_is_validated_and_deduplicated() -> None:
    items = make_items(3)
    first = make_article(items[:1], slug="chatgpt-ads-89-countries")
    assert first.slug == "chatgpt-ads-89-countries"
    slugs = [
        svc.create_article(
            Actor.EDITOR,
            item_ids=[item.pk],
            title="t",
            summary="",
            category=first.category.slug,
            slug="chatgpt-ads-89-countries",
        ).slug
        for item in items[1:]
    ]
    assert slugs == ["chatgpt-ads-89-countries-2", "chatgpt-ads-89-countries-3"]


@pytest.mark.parametrize("bad", ["ab", "한글-제목", "1-300", "Has-Upper", "a--b", "-abc", "a" * 61])
def test_editor_slug_format_rejected(bad: str) -> None:
    items = make_items(1)
    svc.add_category("경제")
    with pytest.raises(svc.InvalidArticleSlug):
        svc.create_article(
            Actor.EDITOR,
            item_ids=[items[0].pk],
            title="t",
            summary="",
            category=svc.list_categories()[0].slug,
            slug=bad,
        )
    assert Article.objects.count() == 0


def test_auto_slug_needs_three_latin_letters() -> None:
    assert svc.make_slug("한글 제목 12", Article, "news").startswith("news-")
    assert svc.make_slug("AI 4", Article, "news").startswith("news-")
    assert svc.make_slug("Rates held 2", Article, "news") == "rates-held-2"


def test_article_slug_change_keeps_old_slug_for_redirect() -> None:
    article = make_article(make_items(1), slug="old-name", published=True)
    svc.record_article_slug_change(article, "new-name")
    article.save()
    assert svc.find_article_by_old_slug("old-name").slug == "new-name"  # type: ignore[union-attr]
    assert svc.unique_article_slug("old-name") == "old-name-2"


def test_admin_changes_article_slug_and_keeps_old(admin_client: Client) -> None:
    article = make_article(make_items(1), slug="before-change")
    url = reverse("admin:articles_article_change", args=[article.pk])
    data = {
        "title": article.title,
        "summary": "",
        "slug": "after-change",
        "category": article.category.pk,
        "image_url": "",
        "published_at_0": "2026-01-01",
        "published_at_1": "00:00:00",
        "article_items-TOTAL_FORMS": "0",
        "article_items-INITIAL_FORMS": "0",
        "commentary-TOTAL_FORMS": "0",
        "commentary-INITIAL_FORMS": "0",
    }
    assert admin_client.post(url, data).status_code == 302
    article.refresh_from_db()
    assert article.slug == "after-change"
    assert svc.find_article_by_old_slug("before-change") is None  # 미게시
    assert article.old_slugs.get().slug == "before-change"
    data["slug"] = "Bad Slug"
    assert admin_client.post(url, data).status_code == 200  # 검증 오류로 다시 그림


# --- 발행 시각(#56) ---


def _at(item: Item, published_at: datetime | None) -> Item:
    Item.objects.filter(pk=item.pk).update(published_at=published_at)
    item.refresh_from_db()
    return item


def test_published_at_is_earliest_bundled_item_time() -> None:
    a, b = make_items()
    early, late = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    _at(a, late)
    _at(b, early)
    article = make_article([a, b])
    assert article.published_at == early


def test_published_at_falls_back_to_created_at_without_item_times() -> None:
    article = make_article(make_items())
    assert article.published_at == article.created_at


def test_future_item_time_is_ignored() -> None:
    a, b = make_items()
    _at(a, timezone.now() + timedelta(days=2))
    article = make_article([a])
    assert article.published_at == article.created_at
    ok = datetime(2026, 1, 1, tzinfo=UTC)
    _at(b, ok)
    svc.update_article(Actor.EDITOR, article.pk, add_item_ids=[b.pk])
    article.refresh_from_db()
    assert article.published_at == ok


def test_add_item_only_moves_earlier() -> None:
    a, b, c = make_items(3)
    mid = datetime(2026, 1, 5, tzinfo=UTC)
    _at(a, mid)
    _at(b, datetime(2026, 1, 9, tzinfo=UTC))
    _at(c, datetime(2026, 1, 1, tzinfo=UTC))
    article = make_article([a])
    svc.update_article(Actor.EDITOR, article.pk, add_item_ids=[b.pk])
    article.refresh_from_db()
    assert article.published_at == mid
    svc.update_article(Actor.EDITOR, article.pk, add_item_ids=[c.pk])
    article.refresh_from_db()
    assert article.published_at == datetime(2026, 1, 1, tzinfo=UTC)


def test_owner_override_in_admin_is_kept(admin_client: Client) -> None:
    a, b = make_items()
    _at(b, datetime(2026, 1, 1, tzinfo=UTC))
    article = make_article([a])
    url = reverse("admin:articles_article_change", args=[article.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    Article.objects.filter(pk=article.pk).update(published_at=datetime(2026, 3, 1, tzinfo=UTC))
    Article.objects.filter(pk=article.pk).update(published_at_overridden=True)
    svc.update_article(Actor.EDITOR, article.pk, add_item_ids=[b.pk])
    article.refresh_from_db()
    assert article.published_at == datetime(2026, 3, 1, tzinfo=UTC)


def test_public_list_orders_by_published_at_then_id() -> None:
    a, b = make_items()
    _at(a, datetime(2026, 1, 1, tzinfo=UTC))
    _at(b, datetime(2026, 1, 9, tzinfo=UTC))
    old = make_article([a], title="옛 글", published=True, slug="old-news")
    cat = svc.list_categories()[0].slug
    new = svc.create_article(
        Actor.EDITOR,
        item_ids=[b.pk],
        title="새 글",
        summary="s",
        category=cat,
        published=True,
        slug="new-news",
    )
    Article.objects.filter(pk=old.pk).update(created_at=timezone.now() + timedelta(hours=1))
    assert [x.pk for x in svc.public_list()] == [new.pk, old.pk]
