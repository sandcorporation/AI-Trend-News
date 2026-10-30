from datetime import datetime, timedelta
from decimal import Decimal

from django.db import models

# 이 시간이 지나도록 종료 보고가 없는 실행은 미실행으로 본다(경보 #10).
ABANDON_AFTER = timedelta(hours=24)


class EditorRun(models.Model):
    """편집자 실행 하나(GLOSSARY: 편집자 실행).

    루틴(`mode=routine`)은 MCP `start_editor_run`이 만들고 `finish_editor_run`이 종료 시각·건수·
    메모를 채운다. 파이프라인(`shadow`/`live`, ADR 0006)은 `run_editor`가 편집 시각 슬롯마다 하나
    만들고 호출 하나하나를 `EditorCall`로 남긴다.
    """

    class State(models.TextChoices):
        RUNNING = "running", "진행 중"
        FINISHED = "finished", "종료"
        ABANDONED = "abandoned", "미실행(종료 보고 없음)"

    class Mode(models.TextChoices):
        ROUTINE = "routine", "루틴(MCP)"
        SHADOW = "shadow", "그림자"
        LIVE = "live", "게시"

    class Status(models.TextChoices):
        RUNNING = "running", "진행 중"
        FINISHED = "finished", "종료"
        FAILED = "failed", "실패"
        SKIPPED_BUDGET = "skipped_budget", "예산 초과로 건너뜀"

    mode = models.CharField("모드", max_length=10, choices=Mode.choices, default=Mode.ROUTINE)
    slot = models.CharField(
        "슬롯",
        max_length=16,
        null=True,
        blank=True,
        unique=True,
        help_text="파이프라인 실행의 편집 시각(KST 날짜+시, 예 2026-10-30T06). 루틴은 비움",
    )
    status = models.CharField(
        "실행 상태", max_length=20, choices=Status.choices, default=Status.RUNNING
    )
    cost_usd = models.DecimalField(
        "추정 비용(US$)", max_digits=10, decimal_places=6, default=Decimal(0)
    )
    started_at = models.DateTimeField("시작 시각", auto_now_add=True)
    finished_at = models.DateTimeField("종료 시각", null=True, blank=True)
    created = models.PositiveIntegerField("생성 건수", default=0)
    updated = models.PositiveIntegerField("갱신 건수", default=0)
    discarded = models.PositiveIntegerField("버림 건수", default=0)
    note = models.TextField("메모", blank=True)

    class Meta:
        verbose_name = "편집자 실행"
        verbose_name_plural = "편집자 실행"
        ordering = ["-started_at", "-id"]

    def __str__(self) -> str:
        return f"실행 {self.pk} ({self.started_at:%Y-%m-%d %H:%M})"

    def state(self, now: datetime) -> EditorRun.State:
        if self.finished_at is not None:
            return self.State.FINISHED
        if now - self.started_at >= ABANDON_AFTER:
            return self.State.ABANDONED
        return self.State.RUNNING


class EditorCall(models.Model):
    """파이프라인의 LLM 호출 하나(설계 §6). 토큰·캐시 토큰·추정 비용·입출력 JSON·상태를 남긴다.

    `input`은 입력 요약(id·길이)만 — 원문 본문은 저장하지 않는다. `input`·`output`은 30일 뒤
    `prune_items`가 비우고 토큰·비용 칸은 남긴다(월 합계용).
    """

    class Stage(models.TextChoices):
        CLASSIFY = "classify", "분류"
        WRITE_NEW = "write_new", "새 기사 작성"
        WRITE_UPDATE = "write_update", "기사 갱신"
        COMMENTARY = "commentary", "분석·포스트"

    class Status(models.TextChoices):
        QUEUED = "queued", "대기"
        SUBMITTED = "submitted", "제출"
        SUCCEEDED = "succeeded", "성공"
        SAVED = "saved", "저장됨"
        FAILED = "failed", "실패"
        EXPIRED = "expired", "만료"
        SKIPPED = "skipped", "건너뜀"

    run = models.ForeignKey(EditorRun, on_delete=models.CASCADE, related_name="calls")
    stage = models.CharField("단계", max_length=20, choices=Stage.choices)
    key = models.CharField("키", max_length=64, unique=True, help_text="배치 custom_id로도 쓴다")
    model = models.CharField("모델", max_length=64)
    effort = models.CharField("effort", max_length=10, blank=True)
    via_batch = models.BooleanField("배치", default=False)
    batch_id = models.CharField("배치 id", max_length=100, blank=True)
    submitted_at = models.DateTimeField(
        "배치 제출 시각", null=True, blank=True, help_text="24시간 넘으면 만료로 끝낸다"
    )
    prompt_version = models.CharField("프롬프트 판", max_length=12, blank=True)
    status = models.CharField("상태", max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempt = models.PositiveSmallIntegerField("재요청", default=0)
    item_ids = models.JSONField("수집물 id", default=list, blank=True)
    article = models.ForeignKey(
        "articles.Article", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    input = models.JSONField("입력 요약", default=dict, blank=True)
    output = models.JSONField("출력", null=True, blank=True)
    stop_reason = models.CharField("stop_reason", max_length=40, blank=True)
    error = models.TextField("오류", blank=True)
    input_tokens = models.PositiveIntegerField("입력 토큰", default=0)
    output_tokens = models.PositiveIntegerField("출력 토큰", default=0)
    cache_creation_input_tokens = models.PositiveIntegerField("캐시 쓰기 토큰", default=0)
    cache_read_input_tokens = models.PositiveIntegerField("캐시 읽기 토큰", default=0)
    cost_usd = models.DecimalField(
        "추정 비용(US$)", max_digits=10, decimal_places=6, default=Decimal(0)
    )
    created_at = models.DateTimeField("만든 시각", auto_now_add=True)
    finished_at = models.DateTimeField("끝난 시각", null=True, blank=True)

    class Meta:
        verbose_name = "편집 호출"
        verbose_name_plural = "편집 호출"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.key


class Alert(models.Model):
    """운영 경보 상태(#10): 같은 조건으로 되풀이해 보내지 않으려고 종류·대상별로 한 줄만 둔다.

    조건이 처음 참이 되면 활성으로 켜고 보낸다. 조건이 풀리면 끄고 「복구」를 한 번 보낸다.
    """

    class Kind(models.TextChoices):
        EDITOR_MISSING = "editor_missing", "편집자 미실행"
        SOURCE_FAILING = "source_failing", "소스 연속 실패"
        PREDICTIONS_DUE = "predictions_due", "전망 평가 대기"
        EDITOR_BUDGET = "editor_budget", "편집 예산 초과"
        EDITOR_BATCH_STALE = "editor_batch_stale", "편집 배치 지연"

    kind = models.CharField("종류", max_length=30, choices=Kind.choices)
    target = models.CharField(
        "대상", max_length=100, blank=True, help_text="소스 id 등. 없으면 빈 값"
    )
    active = models.BooleanField("활성", default=False)
    last_sent_at = models.DateTimeField("마지막 발송 시각", null=True, blank=True)

    class Meta:
        verbose_name = "경보"
        verbose_name_plural = "경보"
        constraints = [models.UniqueConstraint(fields=["kind", "target"], name="alert_kind_target")]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.target}".strip()
