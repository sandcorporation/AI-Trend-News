from typing import Any

from django.core.management.base import BaseCommand

from adapters.clock import get_clock
from services.inquiries import resend_pending


class Command(BaseCommand):
    help = (
        "전송 대기 중인 문의를 portfolio로 다시 보내고 성공한 건은 지운다"
        "(영구실패는 건드리지 않는다)."
    )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        sent, waiting = resend_pending(get_clock().now())
        self.stdout.write(f"문의 재전송: 보냄 {sent}건, 대기 {waiting}건")
