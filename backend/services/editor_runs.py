"""편집자 실행(#8): MCP의 start_editor_run / finish_editor_run이 부른다.

루틴 실행을 새로 열 때 `STALE_AFTER`(2시간) 넘은 미종료 루틴 실행은 실패로 닫는다.
종료 보고 없이 `ABANDON_AFTER`(24시간)가 지난 실행은 미실행으로 본다. 미실행 경보(#10)는
`last_finished_run()`과 `EditorRun.state(now)`를 읽는다. 실패로 닫은 루틴 실행은 마지막 종료로
세지 않는다.
"""

from datetime import timedelta
from typing import NamedTuple

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from editor.models import ABANDON_AFTER, EditorRun

__all__ = [
    "ABANDON_AFTER",
    "NOTE_APPEND_WINDOW",
    "NOTE_MAX_LENGTH",
    "STALE_AFTER",
    "EditorRunError",
    "StartedRun",
    "append_run_note",
    "counted_runs",
    "finish_run",
    "last_finished_run",
    "start_run",
]

# 루틴 실행이 이만큼 지나도록 끝나지 않았으면 다음 실행을 시작할 때 닫는다.
STALE_AFTER = timedelta(hours=2)
# 끝난 루틴 실행에 메모를 덧붙일 수 있는 시간과 메모 전체 길이 상한.
NOTE_APPEND_WINDOW = timedelta(hours=6)
NOTE_MAX_LENGTH = 2000


class EditorRunError(Exception):
    """실행 보고 규칙 위반. 메시지는 MCP가 그대로 돌려준다."""


class StartedRun(NamedTuple):
    run: EditorRun
    closed_run_ids: list[int]


@transaction.atomic
def start_run() -> StartedRun:
    """루틴 실행을 열고, 종료 보고 없이 `STALE_AFTER` 넘게 지난 루틴 실행을 실패로 닫는다.

    그보다 새것은 동시에 도는 실행일 수 있어 둔다. 그림자·게시 실행은 파이프라인이 관리한다.
    닫힌 실행은 종료 시각이 생기지만 미실행 경보는 종료로 세지 않는다(`last_finished_run`).
    """
    run = EditorRun.objects.create()
    now = timezone.now()
    stale = list(
        EditorRun.objects.select_for_update()
        .filter(
            mode=EditorRun.Mode.ROUTINE,
            finished_at__isnull=True,
            started_at__lt=now - STALE_AFTER,
        )
        .order_by("id")
    )
    for old in stale:
        old.status = EditorRun.Status.FAILED
        old.finished_at = now
        old.note = f"종료 보고 없이 끝남 — 실행 {run.pk} 시작 때 닫음"
        old.save(update_fields=["status", "finished_at", "note"])
    return StartedRun(run, [old.pk for old in stale])


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


@transaction.atomic
def append_run_note(run_id: int, note: str) -> EditorRun:
    """끝난 루틴 실행의 메모 뒤에 덧붙인다(종료 뒤에 쓰는 AI 분석의 결과)."""
    try:
        run = EditorRun.objects.select_for_update().get(pk=run_id, mode=EditorRun.Mode.ROUTINE)
    except EditorRun.DoesNotExist:
        raise EditorRunError(f"없는 루틴 실행입니다: {run_id}") from None
    if run.finished_at is None:
        raise EditorRunError(f"실행 {run_id}은(는) 아직 끝나지 않았습니다.")
    if timezone.now() - run.finished_at > NOTE_APPEND_WINDOW:
        hours = int(NOTE_APPEND_WINDOW.total_seconds() // 3600)
        raise EditorRunError(f"실행 {run_id}은(는) 끝난 지 {hours}시간이 지났습니다.")
    if not note.strip():
        raise EditorRunError("덧붙일 메모가 비어 있습니다.")
    combined = f"{run.note}\n{note.strip()}" if run.note else note.strip()
    if len(combined) > NOTE_MAX_LENGTH:
        raise EditorRunError(f"메모 전체는 {NOTE_MAX_LENGTH}자를 넘을 수 없습니다.")
    run.note = combined
    run.save(update_fields=["note"])
    return run


def counted_runs() -> QuerySet[EditorRun]:
    """미실행 경보가 세는 실행: 그림자 실행은 뺀다(루틴이 멈춘 것을 그림자가 가리면 안 된다)."""
    return EditorRun.objects.exclude(mode=EditorRun.Mode.SHADOW)


def last_finished_run() -> EditorRun | None:
    """마지막으로 끝난 실행. 실패로 닫은 루틴 실행은 뺀다 — 루틴이 매번 멈춰도 다음 실행이
    앞 실행을 닫으므로, 세면 미실행 경보가 켜지지 않는다. 게시 실행의 실패는 센다.
    """
    return (
        counted_runs()
        .filter(finished_at__isnull=False)
        .exclude(mode=EditorRun.Mode.ROUTINE, status=EditorRun.Status.FAILED)
        .order_by("-finished_at")
        .first()
    )
