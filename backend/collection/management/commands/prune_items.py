from typing import Any

from django.core.management.base import BaseCommand

from services.retention import prune_items


class Command(BaseCommand):
    help = "24시간 지난 미처리는 버림 처리, 30일 지난 미처리·버림은 지운다(묶인 것은 그대로)."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        result = prune_items()
        self.stdout.write(
            f"수집물 정리: 자동 버림 {result.discarded}개, "
            f"지움 {result.deleted}개, 남김 {result.kept}개"
        )
