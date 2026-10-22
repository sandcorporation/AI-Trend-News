from typing import Any

from django.core.management.base import BaseCommand

from services.collection import collect_all


class Command(BaseCommand):
    help = "켜진 소스를 모두 수집한다(소스별 독립 실패)."

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        results = collect_all()
        for r in results:
            self.stdout.write(f"{r.source.name}: {r.message}")
        failed = sum(1 for r in results if not r.ok)
        self.stdout.write(f"소스 {len(results)}개, 실패 {failed}개")
