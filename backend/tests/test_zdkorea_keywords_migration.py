"""#162: 0007 ZDNet Korea include_any 키워드 보강(덧붙이기만, 중복 없음)."""

import importlib

import pytest
from django.apps import apps

from collection.models import Source

pytestmark = pytest.mark.django_db

mig = importlib.import_module("collection.migrations.0007_zdkorea_include_more")


def _zd(rules: dict[str, object]) -> Source:
    return Source.objects.create(name="ZDNet Korea", url=mig.ZDKOREA_URL, filter_rules=rules)


def test_appends_missing_keywords_keeping_owner_edits() -> None:
    source = _zd({"include_any": ["AI", "쿠팡", "대표가 더한 말"], "exclude_url": ["/x/"]})
    other = Source.objects.create(name="o", url="https://o.example.com/f", filter_rules={})
    mig.forwards(apps, None)  # 시험: 지금 앱 레지스트리로 부름
    source.refresh_from_db()
    include = source.filter_rules["include_any"]
    assert include[:3] == ["AI", "쿠팡", "대표가 더한 말"]  # 기존 값·순서 그대로
    assert include.count("쿠팡") == 1 and len(include) == len(set(include))
    assert set(mig.ADDED) <= set(include) and "넷마블" in include and "한컴" in include
    assert source.filter_rules["exclude_url"] == ["/x/"]
    # 다시 돌려도 중복 없음
    mig.forwards(apps, None)
    source.refresh_from_db()
    assert source.filter_rules["include_any"] == include
    other.refresh_from_db()
    assert other.filter_rules == {}


def test_backwards_removes_only_added_and_skips_without_include() -> None:
    source = _zd({"include_any": ["AI", "대표가 더한 말"]})
    mig.forwards(apps, None)
    mig.backwards(apps, None)
    source.refresh_from_db()
    assert source.filter_rules == {"include_any": ["AI", "대표가 더한 말"]}
    Source.objects.filter(pk=source.pk).update(filter_rules={"exclude_url": ["/a/"]})
    mig.forwards(apps, None)
    source.refresh_from_db()
    assert source.filter_rules == {"exclude_url": ["/a/"]}
