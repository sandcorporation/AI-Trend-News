"""게시 모드의 수집물 예약(#128, 설계 §5). 모델만 읽는다(분류·정리 어디서 불러도 순환 없음).

게시(`live`) 실행의 1단계가 새 기사·갱신으로 정한 수집물은 작성 호출(`EditorCall.item_ids`)에
적힌 채 미처리로 남는다. 그 호출이 끝나기 전에는 (a) 다음 실행의 분류 입력과
(b) `prune_items`의 24시간 자동 버림에서 뺀다. 호출이 저장·실패·만료·건너뜀으로 끝나면 풀린다.
그림자는 예약하지 않는다.

모드를 `off`로 되돌리면 열린 실행이 더는 나아가지 않으므로, 시작한 지 `RESERVE_MAX_AGE`가 지난
실행의 예약은 풀린 것으로 본다(되돌린 뒤 루틴이 그 수집물을 처리할 수 있게).
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from editor.models import EditorCall, EditorRun

RESERVE_MAX_AGE = timedelta(hours=48)  # 배치 만료(제출 뒤 24시간)보다 넉넉히
WRITE_STAGES = (EditorCall.Stage.WRITE_NEW, EditorCall.Stage.WRITE_UPDATE)
# 성공(`succeeded`)은 게시 모드에서 저장 직전의 잠깐뿐이지만, 그 사이도 예약으로 본다.
RESERVING = (EditorCall.Status.QUEUED, EditorCall.Status.SUBMITTED, EditorCall.Status.SUCCEEDED)


def _ints(values: Iterable[object]) -> set[int]:
    return {v for v in values if isinstance(v, int)}


def reserved_item_ids(now: datetime) -> set[int]:
    """끝나지 않은 게시 모드 작성 호출이 잡고 있는 수집물 id."""
    rows = EditorCall.objects.filter(
        run__mode=EditorRun.Mode.LIVE,
        run__status=EditorRun.Status.RUNNING,
        run__started_at__gte=now - RESERVE_MAX_AGE,
        stage__in=WRITE_STAGES,
        status__in=RESERVING,
    ).values_list("item_ids", flat=True)
    reserved: set[int] = set()
    for item_ids in rows:
        if isinstance(item_ids, list):
            reserved |= _ints(item_ids)
    return reserved
