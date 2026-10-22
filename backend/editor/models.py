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
