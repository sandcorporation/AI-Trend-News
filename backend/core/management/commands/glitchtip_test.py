"""오류 수집(GlitchTip) 연동 확인: 시험 예외 하나를 보내고 이벤트 id를 출력한다(#168).

`manage.py glitchtip_test [--message …]`. DSN이 없으면 「DSN 없음」을 출력하고 종료 코드 1.
DB를 건드리지 않는다.
"""

from datetime import date

import sentry_sdk
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.observability import sentry_enabled


class GlitchTipOnboardingTest(Exception):
    """연동 확인용으로 일부러 던지는 예외. 자동화가 이 이름으로 시험 오류를 알아본다."""


class Command(BaseCommand):
    help = "GlitchTip 연동 시험 오류(GlitchTipOnboardingTest) 하나를 보내고 이벤트 id를 출력한다."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--message", default="", help="예외 문구(기본 「연동 시험 foresight <날짜>」)"
        )

    def handle(self, *args: object, **options: str) -> None:
        if not sentry_enabled():
            raise CommandError(
                "DSN 없음: SENTRY_DSN_BACKEND가 비어 있어 보내지 않았다", returncode=1
            )
        message = options["message"] or f"연동 시험 foresight {date.today().isoformat()}"
        try:
            raise GlitchTipOnboardingTest(message)
        except GlitchTipOnboardingTest as exc:
            event_id = sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=10)
        if event_id is None:
            raise CommandError(
                "이벤트가 만들어지지 않았다(before_send가 버렸거나 SDK가 꺼짐)", returncode=1
            )
        self.stdout.write(f"보냄: {event_id}")
