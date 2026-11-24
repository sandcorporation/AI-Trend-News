"""#191: 미평가 예측의 문장 속 명시 날짜가 기한보다 이르면 기한을 그 날짜로 당긴다(갱신만)."""

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.utils import timezone

from services.claim_dates import deadline


def forwards(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    Prediction = apps.get_model("articles", "Prediction")
    for row in Prediction.objects.filter(verdict="pending").select_related("commentary"):
        created = timezone.localtime(row.commentary.created_at).date()
        stated = deadline(row.claim, created)
        if stated is not None and stated < row.due_date:
            row.due_date = stated
            row.save(update_fields=["due_date"])


class Migration(migrations.Migration):
    dependencies = [("articles", "0012_commentary_watchpoints")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
