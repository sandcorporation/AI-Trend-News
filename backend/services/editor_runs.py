"""편집자 실행(#8): MCP의 start_editor_run / finish_editor_run이 부른다.

종료 보고 없이 `ABANDON_AFTER`(24시간)가 지난 실행은 미실행으로 본다. 미실행 경보(#10)는
`last_finished_run()`과 `EditorRun.state(now)`를 읽는다.
"""

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from editor.models import ABANDON_AFTER, EditorRun

__all__ = [
    "ABANDON_AFTER",
    "EditorRunError",
    "counted_runs",
    "finish_run",
    "last_finished_run",
    "start_run",
]


class EditorRunError(Exception):
    """실행 보고 규칙 위반. 메시지는 MCP가 그대로 돌려준다."""


def start_run() -> EditorRun:
    return EditorRun.objects.create()


@transaction.atomic
def finish_run(
    run_id: int, *, created: int, updated: int, discarded: int, note: str = ""
) -> EditorRun:
    try:
        run = EditorRun.objects.select_for_update().get(pk=run_id)
    except EditorRun.DoesNotExist:
        raise EditorRunError(f"없는 실행입니다: {run_id}") from None
    if run.finished_at is not None:
        raise EditorRunError(
            f"실행 {run_id}은(는) 이미 {run.finished_at:%Y-%m-%d %H:%M}에 끝났습니다."
        )
    if min(created, updated, discarded) < 0:
        raise EditorRunError("건수는 0 이상이어야 합니다.")
    run.finished_at = timezone.now()
    run.created, run.updated, run.discarded, run.note = created, updated, discarded, note
    run.status = EditorRun.Status.FINISHED
    run.save()
    return run


def counted_runs() -> QuerySet[EditorRun]:
    """미실행 경보가 세는 실행: 그림자 실행은 뺀다(루틴이 멈춘 것을 그림자가 가리면 안 된다)."""
    return EditorRun.objects.exclude(mode=EditorRun.Mode.SHADOW)


def last_finished_run() -> EditorRun | None:
    return counted_runs().filter(finished_at__isnull=False).order_by("-finished_at").first()
