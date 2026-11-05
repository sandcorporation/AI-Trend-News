"""운영 설정(#164): admin에서 바꾸고 재시작 없이 반영하는 설정.

규칙은 `services/runtime_settings.py`.
"""

from django.db import models


class RuntimeSetting(models.Model):
    """설정 한 행. 비밀(`secret`)은 `value`에 Fernet 암호문만 담는다 — 평문은 DB에 없다."""

    name = models.CharField("이름", max_length=64, unique=True)
    value = models.TextField("값")
    updated_at = models.DateTimeField("마지막 변경", auto_now=True)
    updated_by = models.CharField("변경자", max_length=150, blank=True)

    class Meta:
        verbose_name = "운영 설정"
        verbose_name_plural = "운영 설정"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name  # 값은 내보내지 않는다

    def __repr__(self) -> str:
        return f"<RuntimeSetting {self.name}>"


class RuntimeSettingsVersion(models.Model):
    """한 행(pk=1). 저장·지우기마다 1씩 올라 다른 프로세스가 캐시를 다시 읽게 한다."""

    version = models.PositiveBigIntegerField(default=0)

    def __str__(self) -> str:
        return f"v{self.version}"


class RuntimeSettingChange(models.Model):
    """변경 이력. 값은 남기지 않는다."""

    class Action(models.TextChoices):
        SET = "set", "설정"
        CLEAR = "clear", "지우기"

    name = models.CharField("이름", max_length=64)
    action = models.CharField("동작", max_length=8, choices=Action.choices)
    changed_at = models.DateTimeField("시각", auto_now_add=True)
    changed_by = models.CharField("변경자", max_length=150, blank=True)

    class Meta:
        verbose_name = "운영 설정 변경 이력"
        verbose_name_plural = "운영 설정 변경 이력"
        ordering = ("-changed_at", "-id")

    def __str__(self) -> str:
        return f"{self.name} {self.action}"
