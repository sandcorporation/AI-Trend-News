from datetime import datetime, timedelta

from django.db import models

# 이 시간이 지나도록 종료 보고가 없는 실행은 미실행으로 본다(경보 #10).
ABANDON_AFTER = timedelta(hours=24)


class EditorRun(models.Model):
    """편집자가 MCP로 한 번 들어와 일한 실행 하나(GLOSSARY: 편집자 실행).

    `start_editor_run`이 만들고 `finish_editor_run`이 종료 시각·건수·메모를 채운다.
    """

    class State(models.TextChoices):
        RUNNING = "running", "진행 중"
        FINISHED = "finished", "종료"
        ABANDONED = "abandoned", "미실행(종료 보고 없음)"

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

    def state(self, now: datetime) -> "EditorRun.State":
        if self.finished_at is not None:
            return self.State.FINISHED
        if now - self.started_at >= ABANDON_AFTER:
            return self.State.ABANDONED
        return self.State.RUNNING


class Alert(models.Model):
    """운영 경보 상태(#10): 같은 조건으로 되풀이해 보내지 않으려고 종류·대상별로 한 줄만 둔다.

    조건이 처음 참이 되면 활성으로 켜고 보낸다. 조건이 풀리면 끄고 「복구」를 한 번 보낸다.
    """

    class Kind(models.TextChoices):
        EDITOR_MISSING = "editor_missing", "편집자 미실행"
        SOURCE_FAILING = "source_failing", "소스 연속 실패"

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
