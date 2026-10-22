from typing import Any

from django.core.management.base import BaseCommand

from services.retention import prune_items


class Command(BaseCommand):
    help = "30일 지난 미처리·버림 수집물을 지운다(묶인 수집물과 SeenUrl은 남긴다)."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        result = prune_items()
        self.stdout.write(f"수집물 정리: 지움 {result.deleted}개, 남김 {result.kept}개")
