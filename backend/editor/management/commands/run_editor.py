from typing import Any

from django.core.management.base import BaseCommand, CommandError

from adapters.clock import get_clock
from services.editor_pipeline import run_editor
from services.llm_pricing import UnknownModel


class Command(BaseCommand):
    help = (
        "편집 시각(EDITOR_RUN_HOURS, KST)이면 편집자 파이프라인 실행을 연다. "
        "그림자 모드는 분류 결과를 EditorCall에만 남긴다. 꺼짐·키 없음이면 기록만 하고 건너뛴다."
    )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        try:
            message = run_editor(get_clock().now())
        except (UnknownModel, ValueError) as error:
            raise CommandError(f"편집 파이프라인을 시작하지 않음: {error}") from None
        self.stdout.write(message)
