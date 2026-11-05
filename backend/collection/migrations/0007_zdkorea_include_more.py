"""#162: ZDNet Korea `include_any`에 IT 산업 경영·실적·인사 기사를 살릴 키워드를 덧붙인다.

대표가 admin에서 고친 값을 지우지 않게 빠진 키워드만 끝에 덧붙인다(중복 없음).
`include_any`가 없으면(대표가 지웠으면) 건드리지 않는다. backwards는 이 키워드만 뺀다.
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

ZDKOREA_URL = "https://feeds.feedburner.com/zdkorea"
ADDED = [
    "게임",
    "넷마블",
    "넥슨",
    "엔씨",
    "크래프톤",
    "한컴",
    "쿠팡",
    "배민",
    "배달의민족",
    "토스",
    "당근",
    "핀테크",
    "이커머스",
    "앱",
    "HP",
    "IBM",
    "오라클",
    "델",
    "넷플릭스",
    "유튜브",
    "틱톡",
    "웹툰",
    "데이터",
    "전자",
    "디스플레이",
    "배터리",
]


def forwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    source = apps.get_model("collection", "Source")
    for s in source.objects.filter(url=ZDKOREA_URL):
        rules = s.filter_rules if isinstance(s.filter_rules, dict) else {}
        current = rules.get("include_any")
        if not isinstance(current, list):
            continue
        missing = [k for k in ADDED if k not in current]
        if missing:
            s.filter_rules = {**rules, "include_any": [*current, *missing]}
            s.save(update_fields=["filter_rules"])


def backwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    source = apps.get_model("collection", "Source")
    for s in source.objects.filter(url=ZDKOREA_URL):
        rules = s.filter_rules if isinstance(s.filter_rules, dict) else {}
        current = rules.get("include_any")
        if not isinstance(current, list):
            continue
        kept = [k for k in current if k not in ADDED]
        if kept != current:
            s.filter_rules = {**rules, "include_any": kept}
            s.save(update_fields=["filter_rules"])


class Migration(migrations.Migration):
    dependencies = [("collection", "0006_ai_filter")]
    operations = [migrations.RunPython(forwards, backwards)]
