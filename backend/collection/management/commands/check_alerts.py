from typing import Any

from django.core.management.base import BaseCommand

from adapters.clock import get_clock
from adapters.discord import get_notifier
from services.alerts import check_alerts


class Command(BaseCommand):
    help = (
        "편집자 미실행·소스 연속 실패를 점검해 Discord로 알린다(조건당 한 번, 풀리면 복구 한 번)."
    )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        sent = check_alerts(get_notifier(), get_clock().now())
        self.stdout.write(f"경보 점검: 보냄 {len(sent)}건")
